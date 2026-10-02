"""command_white 指令访问控制插件。

在 AstrBot 启动时枚举所有已注册指令，并为每个指令 handler 注入
CommandAccessFilter，按「全局白名单QQ / 全局黑名单命令 / 群聊模板 /
群内QQ模板 / 私聊模板」五层优先级规则，细粒度控制指令是否可被群聊/个人触发。

配置以 _conf_schema.json 静态维护，WebUI 管理面板编辑。
"""

from __future__ import annotations

from astrbot.api import logger
from astrbot.api.event.filter import on_plugin_loaded
from astrbot.api.star import Context, Star, register
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.star import command_management
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.filter.command_group import CommandGroupFilter
from astrbot.core.star.star_handler import EventType, star_handlers_registry

from .access_filter import CommandAccessFilter
from .rules import Rules, parse_rules

# 标记属性名，用于避免重复注入（插件重载时）
_INJECTED_FLAG = "_command_white_injected"

# 模块级单例引用，供 on_plugin_loaded 事件处理器调用实例方法
_instance: "CommandWhite | None" = None


@register(
    "astrbot_plugin_command_white",
    "command_white",
    "按全局白名单QQ/全局黑名单命令/群聊模板/群内QQ模板/私聊模板五层优先级规则，细粒度控制指令是否可被群聊/个人触发",
    "v1.0.0",
    "local",
)
class CommandWhite(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.config = config
        self._rules: Rules = Rules()
        self._injected_handlers: set[str] = set()
        global _instance
        _instance = self

    async def initialize(self) -> None:
        """插件激活时：枚举指令、注入过滤器、加载规则。"""
        try:
            await self._rebuild()
            logger.info(
                f"[command_white] 初始化完成，已注入 {len(self._injected_handlers)} 个指令过滤器，"
                f"共 {len(self._rules.all_commands)} 个已注册指令。"
            )
        except Exception as e:
            logger.error(f"[command_white] 初始化失败: {e}")

    @staticmethod
    async def _collect_commands() -> set[str]:
        """枚举当前已注册的全部指令名（含别名），用于 ALL 展开。"""
        all_commands: set[str] = set()
        try:
            descriptors = await command_management.list_commands()
            for desc in descriptors:
                eff = desc.get("effective_command")
                if eff:
                    all_commands.add(eff)
                for alias in desc.get("aliases", []) or []:
                    if alias:
                        all_commands.add(alias)
        except Exception as e:
            logger.warning(f"[command_white] 枚举指令失败: {e}")
        return all_commands

    async def _rebuild(self) -> None:
        """重新枚举指令、重建规则，并为（未注入的）指令 handler 注入过滤器。

        幂等：已注入过过滤器的 handler 不会重复注入；仅增量覆盖新增 handler。
        """
        # 1. 枚举所有已注册指令，建立完整指令名集合（用于 ALL 展开）
        all_commands = await self._collect_commands()

        # 2. 解析规则（ALL 展开范围随之更新）
        self._rules = parse_rules(self.config, all_commands)

        # 3. 为每个指令 handler 注入访问过滤器
        for handler in star_handlers_registry:
            if handler.event_type != EventType.AdapterMessageEvent:
                continue
            primary = self._find_primary_filter(handler.event_filters)
            if primary is None:
                continue
            if getattr(handler, _INJECTED_FLAG, False):
                continue
            access_filter = CommandAccessFilter(primary, self._get_rules)
            handler.event_filters.append(access_filter)
            setattr(handler, _INJECTED_FLAG, True)
            self._injected_handlers.add(handler.handler_full_name)

    def _get_rules(self) -> Rules:
        """返回当前生效的规则（供过滤器调用）。"""
        return self._rules

    @staticmethod
    def _find_primary_filter(
        event_filters: list,
    ) -> CommandFilter | CommandGroupFilter | None:
        """在 handler 的过滤器链中定位主指令过滤器。"""
        for f in event_filters:
            if isinstance(f, (CommandFilter, CommandGroupFilter)):
                return f
        return None

    async def terminate(self) -> None:
        """插件卸载/重载时：移除注入的过滤器。"""
        try:
            for handler in star_handlers_registry:
                if not getattr(handler, _INJECTED_FLAG, False):
                    continue
                handler.event_filters = [
                    f
                    for f in handler.event_filters
                    if not isinstance(f, CommandAccessFilter)
                ]
                delattr(handler, _INJECTED_FLAG)
            self._injected_handlers.clear()
            logger.info("[command_white] 已移除全部注入的指令过滤器。")
        except Exception as e:
            logger.error(f"[command_white] terminate 失败: {e}")
