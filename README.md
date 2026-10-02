# AstrBot 插件：指令白名单控制（command_white）

按「全局白名单QQ号 / 全局黑名单命令 / 群聊模板 / 群内QQ模板 / 私聊模板」五层优先级规则，细粒度控制**哪些指令可以被谁（个人 / 群聊 / 群内某个QQ号）触发**。

- 无需写代码，所有规则在 WebUI 管理面板的 `_conf_schema.json` 静态配置中维护。
- 支持「重复反转」与「ALL 白名单化」两种高级语义。
- 适用于 AstrBot v4.0.0+。

---

## 目录

- [工作原理](#工作原理)
- [安装](#安装)
- [配置说明](#配置说明)
- [规则模型与优先级](#规则模型与优先级)
- [核心语义](#核心语义)
  - [重复反转](#重复反转)
  - [ALL 白名单化](#all-白名单化)
- [配置示例](#配置示例)
- [测试](#测试)

---

## 工作原理

AstrBot 在启动时会把所有 `@register` 指令（含指令组、别名）登记到全局注册表中。本插件：

1. 在 [`initialize()`](main.py:42) 通过 [`command_management.list_commands()`](../../astrbot/core/star/command_management.py:223) 枚举当前所有已注册指令，得到完整指令名集合（用于 `ALL` 展开）。
2. 解析 [`_conf_schema.json`](_conf_schema.json) 中的规则，构建规范化 [`Rules`](rules.py:57)。
3. 为**每一个** `AdapterMessageEvent`（指令处理）handler 注入 [`CommandAccessFilter`](access_filter.py)，挂接到该 handler 的 `event_filters` 过滤器链上（与 AstrBot 自带过滤器为 AND 关系）。
4. 收到指令消息时，过滤器依据[四层规则](#规则模型与优先级)裁决是否放行；被拦截的指令不会进入 handler，也不会被 LLM/其他插件处理。

**动态重建**：插件通过 `on_plugin_loaded` 事件监听新插件加载。每当有插件加载完成，本插件会重新枚举指令、更新 `ALL` 展开范围，并为新插件的指令 handler 增量注入过滤器（已注入的不会重复注入）。因此**运行期间新加载的插件同样受本插件规则约束**，无需重启。

插件卸载或重载时，[`terminate()`](main.py:100) 会移除全部注入的过滤器，不影响系统其余部分。

---

## 安装

将本插件目录复制到 AstrBot 的插件目录（`data/plugins/`）下，并在 WebUI「插件管理」中启用，或热重载现有插件。插件默认不拦截任何指令（空规则 = 全部放行）。

---

## 配置说明

在 WebUI 插件配置页面对应插件的配置面板中编辑：

### 顶层配置

| 键 | 类型 | 说明 |
|---|---|---|
| `global_whitelist_qq` | list | 全局白名单QQ号。命中的QQ号可直接使用所有命令，**不受任何黑名单限制**（最高优先级）。 |
| `global_blacklist_commands` | list | 全局黑名单命令。对所有群 / 私聊生效。 |
| `group_templates` | template_list | 群聊模板，可针对特定群做黑名单控制。 |
| `group_qq_templates` | template_list | 群内QQ号模板，可针对「特定群里的特定QQ号」做黑名单控制。 |
| `private_templates` | template_list | 私聊模板，可针对**私聊**中的特定QQ号做黑名单控制（仅私聊生效，群聊不参与）。 |

> 说明：AstrBot 的 WebUI 配置面板不支持在 `template_list` 内再嵌套 `template_list`（嵌套项会被渲染成普通文本框）。因此「群聊模板」「群内QQ号模板」「私聊模板」均作为**顶层** `template_list` 配置，QQ 模板通过「所属群ID + QQ号」两个字段定位到具体群内的具体QQ号，私聊模板通过「QQ号」字段定位。

### 群聊模板（`group_templates`）

| 键 | 类型 | 说明 |
|---|---|---|
| `group_id` | string | 群ID。留空或重复的模板会被忽略。 |
| `blacklist_mode` | string | `保留` / `替换`。 |
| `blacklist_commands` | list | 该群的黑名单命令。 |

- **保留**：该群的黑名单 = 全局黑名单 **叠加** 本群黑名单（重复的按[反转](#重复反转)处理）。
- **替换**：该群的黑名单 = **仅**本群黑名单（全局黑名单对该群失效）。

### 群内QQ号模板（`group_qq_templates`）

| 键 | 类型 | 说明 |
|---|---|---|
| `group_id` | string | 所属群ID。填 `ALL` 表示对所有群生效（私聊除外）；精确群ID优先于 `ALL`。留空或重复会被忽略。 |
| `qq_id` | string | QQ号。留空或重复会被忽略。 |
| `blacklist_mode` | string | `保留` / `替换` / `替换全局黑名单` / `替换群聊黑名单`。 |
| `blacklist_commands` | list | 该QQ号在所属群内的黑名单命令。 |

> `group_id` 填 `ALL` 时，该 QQ 模板对该 QQ 号在**所有群**生效；私聊（`gid` 为空）不命中任何 QQ 模板。同一 QQ 同时配有 `ALL` 模板与精确群模板时，精确群内使用精确模板，其余群使用 `ALL` 模板。

四种模式的参与源（`G`=全局黑名单，`Tg`=群模板黑名单，`Tu`=QQ模板黑名单）：

| 模式 | 参与源 | 说明 |
|---|---|---|
| `保留` | `G + Tg + Tu` | 全部叠加，重复反转。 |
| `替换` | `Tu` | 仅使用自身黑名单（丢弃全局与群）。 |
| `替换全局黑名单` | `Tg + Tu` | 丢弃全局，保留群模板 + 自身。 |
| `替换群聊黑名单` | `G + Tu` | 丢弃群模板，保留全局 + 自身。 |

---

## 私聊模板（`private_templates`）

| 键 | 类型 | 说明 |
|---|---|---|
| `qq_id` | string | QQ号。填 `ALL` 表示对所有 QQ 号生效；精确 QQ 号优先于 `ALL`。留空或重复会被忽略。 |
| `blacklist_mode` | string | `保留` / `替换`。 |
| `blacklist_commands` | list | 该 QQ 号在**私聊**中的黑名单命令。 |

- 私聊场景（`gid` 为空）：**仅**使用「全局黑名单 + 私聊模板」，群模板 / 群内QQ模板不参与。
- **保留**：该 QQ 在私聊的黑名单 = 全局黑名单 **叠加** 本模板黑名单（重复的按[反转](#重复反转)处理）。
- **替换**：该 QQ 在私聊的黑名单 = **仅**本模板黑名单（全局黑名单对该 QQ 私聊失效）。
- `qq_id` 填 `ALL` 时，该模板对所有 QQ 号的私聊生效（精确 QQ 号模板优先）。

两种模式的参与源（`G`=全局黑名单，`Tp`=私聊模板黑名单）：

| 模式 | 参与源 | 说明 |
|---|---|---|
| `保留` | `G + Tp` | 全部叠加，重复反转。 |
| `替换` | `Tp` | 仅使用自身黑名单（丢弃全局）。 |

---

## 规则模型与优先级

对某条指令、某个 (群ID, QQ号) 的裁决顺序：

```
1. QQ号 ∈ 全局白名单QQ号 ?  ──是──► 放行（最高优先级）
2. 私聊（gid 为空）?
   ├─ 是：仅用「全局黑名单 G + 私聊模板 Tp」
   │      └─ 私聊模板命中：
   │           ├─ 保留：G + Tp
   │           └─ 替换：Tp
   └─ 否（群聊）：
        ├─ 查找命中的群模板
        │   ├─ 未命中：仅用全局黑名单 G
        │   └─ 命中：
        │        ├─ 保留：G + Tg
        │        └─ 替换：Tg
        └─ 查找命中的 QQ 模板（group_qq_templates，按 (群, QQ) 定位；
           所属群ID 填 ALL 通配所有群，精确群ID优先；私聊不命中）
            └─ 命中：
                 ├─ 保留              ：G + Tg + Tu
                 ├─ 替换              ：Tu
                 ├─ 替换全局黑名单    ：Tg + Tu
                 └─ 替换群聊黑名单    ：G + Tu
3. 对各参与源做对称差（重复反转）得到最终黑名单集合
4. 指令在其中 ? 禁止 : 放行
```

> 私聊场景：`gid` 为空，只受「全局白名单QQ / 全局黑名单 / 私聊模板」约束（群模板不参与）。

判定逻辑实现在 [`decide()`](rules.py:103)，对 (命令, 群, QQ, 规则) 无副作用、纯函数，便于测试与复用。

---

## 核心语义

### 重复反转

某条命令出现在 **k 个参与源** 中：

- **k 为奇数** → 禁止
- **k 为偶数** → 放行

例如：全局黑名单含 `help`，某群模板（保留模式）也含 `help`：

- 该群其他成员：`help` 出现 2 次 → 反转，**放行**。
- 未配置该群的普通群：`help` 出现 1 次 → 禁止。
- 若该群内某 QQ 模板（保留模式）再含 `help`：该QQ的 `help` 出现 3 次 → 又变为**禁止**。

### ALL 白名单化

任意黑名单列表（全局 / 群 / QQ / 私聊）中填入 `ALL`，表示「**当前 AstrBot 已注册的全部指令**」。展开后 `ALL` 与本列表中的具体指令分别作为独立参与源参与反转：

- 全局填 `ALL`：所有指令默认被禁 —— 结合群模板「保留」并在黑名单中列出允许的指令，即可实现**群级白名单模式**：
  - 群模板黑名单 `["play"]` + 全局 `ALL`（保留）→ 该群 `play` 出现 2 次反转**放行**，其余指令被 `ALL` 禁止。
- QQ模板 `["ALL", "help"]` + 「替换」→ 该QQ仅 `help` 放行，其余全禁（个人白名单模式）。
- QQ模板「所属群ID」填 `ALL` → 该QQ模板对**所有群**生效（私聊除外）。例如：`group_id: ALL, qq_id: 10002, 模式: 替换, 黑名单: [ALL, "help"]` → 该QQ号在所有群都仅可用 `help`。
- 私聊模板 `qq_id` 填具体 QQ 号 + 黑名单 `["ALL", "help"]` + 「替换」→ 该QQ号在私聊中仅 `help` 可用，其余全禁（私聊个人白名单模式）。
- 私聊模板 `qq_id` 填 `ALL` + 黑名单 `["ALL"]` + 「替换」→ **所有私聊**全部指令被禁（仅内网可用机器人时慎用）。

---

## 配置示例

以下 JSON 是实际保存的配置数据形态（WebUI 面板编辑后自动生成）。示例意图：

- QQ `10001` 是全局白名单，任何命令可用。
- 全局 `play` / `math add` 被禁。
- 群 `g1` 采用「保留」模式，追加禁止 `help`，且 `math add` 因与全局重复而反转放行（`g1` 内其他成员）。
- 群 `g1` 内 QQ `10002`：替换群聊黑名单 → 丢弃群模板的 `help`，保留全局的 `play`/`math add` 与自身的 `add`。
- 私聊 QQ `10003`：替换 → 私聊中仅 `help` 可用（其余全禁），且不受全局黑名单影响。

```json
{
  "global_whitelist_qq": ["10001"],
  "global_blacklist_commands": ["play", "math add"],
  "group_templates": [
    {
      "group_id": "g1",
      "blacklist_mode": "保留",
      "blacklist_commands": ["help", "math add"]
    }
  ],
  "group_qq_templates": [
    {
      "group_id": "g1",
      "qq_id": "10002",
      "blacklist_mode": "替换群聊黑名单",
      "blacklist_commands": ["add"]
    }
  ],
  "private_templates": [
    {
      "qq_id": "10003",
      "blacklist_mode": "替换",
      "blacklist_commands": ["ALL", "help"]
    }
  ]
}
```

---

## 测试

判定引擎为纯函数，已提供 pytest 单元测试（覆盖策略矩阵 / 反转 / ALL / 白名单优先级 / 私聊模板 / raw 配置解析）：

```bash
cd data/plugins/command_white
python -m pytest tests/test_rules.py -v
```

若环境未安装 pytest：

```bash
python -m pip install pytest