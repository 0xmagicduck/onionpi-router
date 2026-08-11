"""Stable internal contracts for platform-specific privileged capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .agent import PrivilegedAgent
from .system import wifi_details


@runtime_checkable
class TorBackend(Protocol):
    def status(self, force: bool = False) -> dict[str, Any]: ...

    def new_identity(self) -> None: ...


@runtime_checkable
class FirewallBackend(Protocol):
    def self_test(self) -> dict[str, Any]: ...

    def restart(self) -> dict[str, Any]: ...


@runtime_checkable
class AccessPointBackend(Protocol):
    def snapshot(self) -> dict[str, Any]: ...

    def restart(self) -> dict[str, Any]: ...


@runtime_checkable
class UpdateBackend(Protocol):
    def state(self) -> dict[str, Any]: ...

    def save_preferences(
        self, channel: str, schedule: str, enabled: bool, apply: bool
    ) -> Any: ...


@dataclass(slots=True)
class RaspberryPiFirewallBackend:
    agent: PrivilegedAgent

    def self_test(self) -> dict[str, Any]:
        return self.agent.submit("self-test-network", timeout=30)

    def restart(self) -> dict[str, Any]:
        return self.agent.submit("restart-firewall")


@dataclass(slots=True)
class RaspberryPiAccessPointBackend:
    agent: PrivilegedAgent
    wifi_interface: str
    upstream_interface: str
    gateway_ip: str

    def snapshot(self) -> dict[str, Any]:
        return wifi_details(
            self.wifi_interface,
            self.upstream_interface,
            self.gateway_ip,
            False,
        )

    def restart(self) -> dict[str, Any]:
        return self.agent.submit("restart-network")


class DemoFirewallBackend:
    def self_test(self) -> dict[str, Any]:
        return {
            "action": "self-test-network",
            "status": "ok",
            "message": "Coupe-circuit validé (démonstration)",
        }

    def restart(self) -> dict[str, Any]:
        return {"action": "restart-firewall", "status": "ok", "message": "Démonstration"}


@dataclass(slots=True)
class DemoAccessPointBackend:
    wifi_interface: str
    upstream_interface: str
    gateway_ip: str

    def snapshot(self) -> dict[str, Any]:
        return wifi_details(
            self.wifi_interface,
            self.upstream_interface,
            self.gateway_ip,
            True,
        )

    def restart(self) -> dict[str, Any]:
        return {"action": "restart-network", "status": "ok", "message": "Démonstration"}
