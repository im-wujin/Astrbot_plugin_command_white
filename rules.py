"""command_white 指令访问控制判定引擎。

实现五层优先级规则模型：
1. 全局白名单QQ号（最高优先级，命中直接放行所有命令）
2. 全局黑名单命令
3. 群聊模板（保留 / 替换）
4. 群内QQ模板（保留 / 替换 / 替换全局黑名单 / 替换群聊黑名单）
5. 私聊模板（保留 / 替换，仅私聊生效）

核心语义：
- 重复反转：某命令出现在 k 个参与源中，k 为奇数 -> 禁止，k 为偶数 -> 放行。
- ALL：在任意黑名单列表中出现时，展开为当前 AstrBot 已注册的全部指令名集合。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

# 特殊标记：代表"所有命令"
ALL = "ALL"

# 群模板黑名单模式
GROUP_MODE_KEEP = "保留"
GROUP_MODE_REPLACE = "替换"

# QQ 模板黑名单模式
QQ_MODE_KEEP = "保留"
QQ_MODE_REPLACE = "替换"
QQ_MODE_REPLACE_GLOBAL = "替换全局黑名单"
QQ_MODE_REPLACE_GROUP = "替换群聊黑名单"

# 私聊模板黑名单模式（私聊无群模板参与源，故仅两种模式）
PRIVATE_MODE_KEEP = "保留"
PRIVATE_MODE_REPLACE = "替换"

# 判定结果
ALLOW = "allow"
BLOCK = "block"


@dataclass
class QQTemplate:
    """群内 QQ 号模板（顶层 template_list，一对 (所属群ID, QQ号) 唯一定位）。"""

    group_id: str = ""
    qq_id: str = ""
    blacklist_mode: str = QQ_MODE_KEEP
    blacklist_commands: list[str] = field(default_factory=list)


@dataclass
class PrivateTemplate:
    """私聊模板（按 QQ 号定位，仅私聊生效）。"""

    qq_id: str = ""
    blacklist_mode: str = PRIVATE_MODE_KEEP
    blacklist_commands: list[str] = field(default_factory=list)


@dataclass
class GroupTemplate:
    """群聊模板。"""

    group_id: str = ""
    blacklist_mode: str = GROUP_MODE_KEEP
    blacklist_commands: list[str] = field(default_factory=list)


@dataclass
class Rules:
    """全部规则配置（已从 _conf_schema.json 解析并规范化）。"""

    global_whitelist_qq: set[str] = field(default_factory=set)
    global_blacklist_commands: list[str] = field(default_factory=list)
    group_templates: list[GroupTemplate] = field(default_factory=list)
    # 群内 QQ 模板（顶层 template_list，group_id + qq_id 唯一定位）
    group_qq_templates: list[QQTemplate] = field(default_factory=list)
    # 私聊模板（顶层 template_list，按 qq_id 定位，仅私聊生效）
    private_templates: list[PrivateTemplate] = field(default_factory=list)

    # 运行时注入：当前 AstrBot 已注册的全部指令名集合，用于 ALL 展开
    all_commands: set[str] = field(default_factory=set)


def _normalize_cmd_list(items: Iterable[str]) -> list[str]:
    """规范化命令列表：去空白、去空项、去重（保持顺序）。"""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        s = str(item).strip()
        if not s:
            continue
        if s in seen:
            continue
        seen.add(s)
        result.append(s)
    return result


def _expand_sources(cmd_list: list[str], all_commands: set[str]) -> list[set[str]]:
    """将命令列表展开为一个或多个独立参与源。

    - ALL 展开为全部指令名，作为一个独立源；
    - 其余具体命令合并为另一个独立源。

    将 ALL 与具体命令拆分为不同源，使「ALL + 某命令」在同一列表内也能
    通过重复反转触发白名单化（某命令出现 2 次 -> 放行，其余被 ALL 禁止）。
    """
    sources: list[set[str]] = []
    if ALL in cmd_list:
        sources.append(set(all_commands))
    others = {c for c in cmd_list if c != ALL}
    if others:
        sources.append(others)
    return sources


def _xor_merge(sources: list[set[str]]) -> set[str]:
    """对多个命令源做对称差（重复反转）：出现奇数次的命令保留。"""
    counter: dict[str, int] = {}
    for src in sources:
        for c in src:
            counter[c] = counter.get(c, 0) + 1
    return {c for c, n in counter.items() if n % 2 == 1}


def decide(cmd: str, gid: str, qid: str, rules: Rules) -> str:
    """判定某条指令对 (群, QQ) 是否放行。

    Args:
        cmd: 指令完整名（含父级指令组，如 "math add"）。
        gid: 群ID（私聊为空字符串）。
        qid: 发送者QQ号。
        rules: 规范化后的规则。

    Returns:
        ALLOW 或 BLOCK。
    """
    # 1. 全局白名单QQ号（最高优先级）
    if qid in rules.global_whitelist_qq:
        return ALLOW

    # 2. 私聊分支：群模板 / 群内QQ模板不参与，仅用「全局黑名单 + 私聊模板」
    if not gid:
        p_sources = _expand_sources(rules.global_blacklist_commands, rules.all_commands)
        p_tpl = _find_private_template(qid, rules.private_templates)
        if p_tpl is not None:
            tp_sources = _expand_sources(
                p_tpl.blacklist_commands, rules.all_commands
            )
            if p_tpl.blacklist_mode == PRIVATE_MODE_KEEP:
                p_sources = p_sources + list(tp_sources)
            else:  # 替换：丢弃全局，仅用自身
                p_sources = list(tp_sources)
        blocked = _xor_merge(p_sources)
        return BLOCK if cmd in blocked else ALLOW

    # 3. 查找命中的群模板
    group_tpl = _find_group_template(gid, rules.group_templates)

    # 4. 计算群层面的参与源（每个列表展开为独立源，ALL 单独成源以支持反转）
    g_sources = _expand_sources(rules.global_blacklist_commands, rules.all_commands)
    tg_sources: list[set[str]] = []

    if group_tpl is None:
        # 群未命中任何模板 -> 仅使用全局黑名单
        sources = list(g_sources)
    else:
        tg_sources = _expand_sources(group_tpl.blacklist_commands, rules.all_commands)
        if group_tpl.blacklist_mode == GROUP_MODE_KEEP:
            sources = list(g_sources) + list(tg_sources)
        else:  # 替换
            sources = list(tg_sources)

    # 5. 查找命中的 QQ 模板（顶层 group_qq_templates，按 (群, QQ) 定位；
    #    所属群ID支持 ALL 通配所有群，精确群ID优先）
    qq_tpl = _find_qq_template(gid, qid, rules.group_qq_templates)
    if qq_tpl is not None:
        tu_sources = _expand_sources(qq_tpl.blacklist_commands, rules.all_commands)
        mode = qq_tpl.blacklist_mode
        if mode == QQ_MODE_KEEP:
            sources = sources + list(tu_sources)
        elif mode == QQ_MODE_REPLACE:
            # 丢弃全局与群，仅用自身
            sources = list(tu_sources)
        elif mode == QQ_MODE_REPLACE_GLOBAL:
            # 丢弃全局，保留群模板部分 + 自身
            sources = list(tg_sources) + list(tu_sources)
        elif mode == QQ_MODE_REPLACE_GROUP:
            # 丢弃群模板部分，保留全局 + 自身
            sources = list(g_sources) + list(tu_sources)

    # 6. 对称差（重复反转）
    blocked = _xor_merge(sources)
    return BLOCK if cmd in blocked else ALLOW


def _find_group_template(gid: str, templates: list[GroupTemplate]) -> GroupTemplate | None:
    for tpl in templates:
        if tpl.group_id and tpl.group_id == gid:
            return tpl
    return None


def _find_qq_template(
    gid: str, qid: str, templates: list[QQTemplate]
) -> QQTemplate | None:
    """在顶层 QQ 模板中定位命中 (群, QQ) 的模板。

    规则：
    - 私聊（gid 为空）不命中任何 QQ 模板。
    - 所属群ID为 ALL 的模板通配所有群（仍要求 QQ 号匹配）。
    - 精确群ID模板优先于 ALL 通配模板；无精确模板时退回 ALL 通配模板。
    """
    if not gid:
        return None

    exact: QQTemplate | None = None
    wildcard: QQTemplate | None = None
    for tpl in templates:
        if not tpl.qq_id or tpl.qq_id != qid:
            continue
        if tpl.group_id == ALL:
            wildcard = tpl
        elif tpl.group_id == gid:
            exact = tpl
    return exact if exact is not None else wildcard


def _find_private_template(
    qid: str, templates: list[PrivateTemplate]
) -> PrivateTemplate | None:
    """在私聊模板中定位命中当前 QQ 号的模板。

    规则：
    - 按 QQ 号精确匹配，仅首个匹配生效。
    - qq_id 填 ALL 表示对所有 QQ 号生效（通配）；精确 QQ 号优先于 ALL 通配。
    """
    if not qid:
        return None

    exact: PrivateTemplate | None = None
    wildcard: PrivateTemplate | None = None
    for tpl in templates:
        if not tpl.qq_id:
            continue
        if tpl.qq_id == ALL:
            wildcard = tpl
        elif tpl.qq_id == qid:
            exact = tpl
    return exact if exact is not None else wildcard


def parse_rules(raw_config: dict, all_commands: set[str]) -> Rules:
    """从 _conf_schema.json 的原始配置解析为规范化 Rules。

    Args:
        raw_config: 插件配置字典（self.config 的原始内容）。
        all_commands: 当前 AstrBot 已注册的全部指令名集合。
    """
    rules = Rules(all_commands=all_commands)

    # 全局白名单QQ
    wl = raw_config.get("global_whitelist_qq", []) or []
    rules.global_whitelist_qq = {str(x).strip() for x in wl if str(x).strip()}

    # 全局黑名单命令
    rules.global_blacklist_commands = _normalize_cmd_list(
        raw_config.get("global_blacklist_commands", []) or []
    )

    # 群聊模板
    for item in raw_config.get("group_templates", []) or []:
        if not isinstance(item, dict):
            continue
        gid = str(item.get("group_id", "")).strip()
        if not gid:
            continue
        group_tpl = GroupTemplate(
            group_id=gid,
            blacklist_mode=str(item.get("blacklist_mode", GROUP_MODE_KEEP)),
            blacklist_commands=_normalize_cmd_list(
                item.get("blacklist_commands", []) or []
            ),
        )
        rules.group_templates.append(group_tpl)

    # 群内 QQ 模板（顶层 template_list，group_id + qq_id 唯一定位）
    for qq_item in raw_config.get("group_qq_templates", []) or []:
        if not isinstance(qq_item, dict):
            continue
        gid = str(qq_item.get("group_id", "")).strip()
        qid = str(qq_item.get("qq_id", "")).strip()
        if not gid or not qid:
            continue
        rules.group_qq_templates.append(
            QQTemplate(
                group_id=gid,
                qq_id=qid,
                blacklist_mode=str(qq_item.get("blacklist_mode", QQ_MODE_KEEP)),
                blacklist_commands=_normalize_cmd_list(
                    qq_item.get("blacklist_commands", []) or []
                ),
            )
        )

    # 私聊模板（顶层 template_list，按 qq_id 定位；qq_id 填 ALL 通配所有 QQ）
    for p_item in raw_config.get("private_templates", []) or []:
        if not isinstance(p_item, dict):
            continue
        qid = str(p_item.get("qq_id", "")).strip()
        if not qid:
            continue
        rules.private_templates.append(
            PrivateTemplate(
                qq_id=qid,
                blacklist_mode=str(
                    p_item.get("blacklist_mode", PRIVATE_MODE_KEEP)
                ),
                blacklist_commands=_normalize_cmd_list(
                    p_item.get("blacklist_commands", []) or []
                ),
            )
        )

    return rules
