from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from app.models.entities import MemberRole


class McpCapabilityKind(StrEnum):
    TOOL = "tool"
    RESOURCE = "resource"
    PROMPT = "prompt"


class McpCapabilityRisk(StrEnum):
    READ_ONLY = "R0"
    SENSITIVE_READ = "R1"
    SIDE_EFFECT_PROPOSAL = "R2"


class McpCapabilityDescriptor(BaseModel):
    """Stable metadata shared by stdio and Streamable HTTP transports."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    kind: McpCapabilityKind
    description: str = Field(min_length=1, max_length=1_000)
    minimum_role: MemberRole
    risk: McpCapabilityRisk


class CapabilityInstaller(Protocol):
    def __call__(self, server: FastMCP) -> None: ...


@dataclass(frozen=True, slots=True)
class RegisteredCapability:
    descriptor: McpCapabilityDescriptor
    install: CapabilityInstaller


class McpCapabilityRegistry:
    """P9 allowlist and the single source of transport-neutral capabilities."""

    def __init__(self) -> None:
        self._items: dict[tuple[McpCapabilityKind, str], RegisteredCapability] = {}
        self._frozen = False

    def register(
        self,
        descriptor: McpCapabilityDescriptor,
        installer: CapabilityInstaller,
    ) -> None:
        if self._frozen:
            raise RuntimeError("MCP capability registry is frozen")
        key = (descriptor.kind, descriptor.name)
        if key in self._items:
            raise ValueError(f"duplicate MCP capability: {descriptor.kind.value}/{descriptor.name}")
        self._items[key] = RegisteredCapability(descriptor, installer)

    def freeze(self) -> None:
        self._frozen = True

    def install_into(self, server: FastMCP) -> None:
        self.freeze()
        for capability in self._items.values():
            capability.install(server)

    def descriptors(self) -> tuple[McpCapabilityDescriptor, ...]:
        return tuple(item.descriptor for item in self._items.values())

    def __iter__(self) -> Iterator[McpCapabilityDescriptor]:
        return iter(self.descriptors())

    def __len__(self) -> int:
        return len(self._items)


def empty_p9_registry() -> McpCapabilityRegistry:
    """Return the B1 registry; business capabilities are added only after B2."""

    return McpCapabilityRegistry()
