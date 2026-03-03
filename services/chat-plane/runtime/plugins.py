from __future__ import annotations

from typing import Dict, List, Protocol

from .models import ChatContext, NodeResult


class NodePlugin(Protocol):
    plugin_id: str

    async def run(self, ctx: ChatContext) -> NodeResult:
        ...


class ToolPlugin(Protocol):
    tool_name: str

    async def invoke(self, payload: Dict) -> Dict:
        ...


class NodeRegistry:
    def __init__(self) -> None:
        self._items: Dict[str, NodePlugin] = {}

    def register(self, plugin: NodePlugin) -> None:
        self._items[plugin.plugin_id] = plugin

    def get(self, plugin_id: str) -> NodePlugin:
        return self._items[plugin_id]

    def list(self) -> List[str]:
        return sorted(self._items.keys())


class ToolRegistry:
    def __init__(self) -> None:
        self._items: Dict[str, ToolPlugin] = {}

    def register(self, plugin: ToolPlugin) -> None:
        self._items[plugin.tool_name] = plugin

    def get(self, tool_name: str) -> ToolPlugin:
        return self._items[tool_name]

    def list(self) -> List[str]:
        return sorted(self._items.keys())


node_registry = NodeRegistry()
tool_registry = ToolRegistry()
