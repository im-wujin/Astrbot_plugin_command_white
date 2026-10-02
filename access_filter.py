"""command_white 指令访问过滤器。

CommandAccessFilter 被注入到每个指令 handler 的 event_filters 链末尾。
在 waking_check 阶段，指令本身匹配通过后，本过滤器做二次裁决：
- 若消息未命中本指令 -> 返回 True（不影响其他指令的匹配）。
- 若命中本指令 -> 用 rules.decide() 判断该 (群, QQ) 是否被黑名单拦截。

由于 waking_check 中多个 filter 为 AND 逻辑，本过滤器放在 CommandFilter 之后，
不会影响指令的唤醒与参数解析，只做访问控制。
"""

from __future__ import annotations

import re
from collections.abc import Callable

from astrbot.core.config import AstrBotConfig
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.star.filter import HandlerFilter
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.filter.command_group import CommandGroupFilter

from .rules import ALLOW, BLOCK, Rules, decide

# 规则提供者：返回当前生效的 Rules（配置可能热更新）
RulesProvider = Callable[[], Rules]


class CommandAccessFilter(HandlerFilter):
    """指令访问控制过滤器。"""

    def __init__(
        self,
        primary_filter: CommandFilter | CommandGroupFilter,
        rules_provider: RulesProvider,
    ) -> None:
        self.primary_filter = primary_filter
        self.rules_provider = rules_provider
        # 该指令的所有完整名（含父级指令组与别名），用于黑名单匹配
        self.command_names: list[str] = list(primary_filter.get_complete_command_names())

    def filter(self, event: AstrMessageEvent, cfg: AstrBotConfig) -> bool:
        # 非唤醒/指令消息，不参与裁决
        if not event.is_at_or_wake_command:
            return True

        # 判断消息是否命中本指令（与 CommandFilter 的匹配逻辑一致）
        message_str = re.sub(r"\s+", " ", event.get_message_str().strip())
        hit = False
        for full_cmd in self.command_names:
            if message_str.startswith(f"{full_cmd} ") or message_str == full_cmd:
                hit = True
                break
        if not hit:
            # 不是本指令，不影响其他指令
            return True

        # 命中本指令，按规则裁决
        rules = self.rules_provider()
        gid = str(event.get_group_id() or "")
        qid = str(event.get_sender_id() or "")

        # 该指令可能有主名与别名，任一被黑名单命中即拦截
        for name in self.command_names:
            if decide(name, gid, qid, rules) == BLOCK:
                return False
        return True
