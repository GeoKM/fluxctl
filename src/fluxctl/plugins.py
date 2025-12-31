"""Simple plugin registry for encoding, layout, filesystem and exporter plugins."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Optional

from .models import LayoutDescriptor


@dataclass
class PluginInfo:
    name: str
    version: str
    entry: object
    description: str


class PluginRegistry:
    def __init__(self) -> None:
        self.encoding: Dict[str, PluginInfo] = {}
        self.layout: Dict[str, LayoutDescriptor] = {}
        self.filesystem: Dict[str, PluginInfo] = {}
        self.exporter: Dict[str, PluginInfo] = {}

    def register_encoding(self, key: str, plugin: PluginInfo) -> None:
        self.encoding[key] = plugin

    def register_layout(self, key: str, descriptor: LayoutDescriptor) -> None:
        self.layout[key] = descriptor

    def register_filesystem(self, key: str, plugin: PluginInfo) -> None:
        self.filesystem[key] = plugin

    def register_exporter(self, key: str, plugin: PluginInfo) -> None:
        self.exporter[key] = plugin

    def get_layout(self, key: str) -> Optional[LayoutDescriptor]:
        return self.layout.get(key)


registry = PluginRegistry()
