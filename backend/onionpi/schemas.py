"""Versioned API schemas used as the source of truth for frontend types."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class ServiceStatus(BaseModel):
    id: str
    label: str
    active: bool


class SystemStatus(BaseModel):
    hostname: str
    cpu_percent: float
    memory_percent: float
    temperature_c: float | None
    storage_percent: float
    storage_used: int
    storage_total: int
    uptime_seconds: int
    services: list[ServiceStatus]


class BridgeStatus(BaseModel):
    use_bridges: bool
    transport: str | None
    bridge_count: int
    known: bool


class TorNodeStatus(BaseModel):
    role: str
    name: str


class TorStatus(BaseModel):
    connected: bool
    bootstrap: int
    summary: str
    circuit: list[TorNodeStatus]
    exit_ip: str | None
    exit_country: str | None
    bridges: BridgeStatus


class NetworkStatus(BaseModel):
    ssid: str
    wifi_interface: str
    upstream_interface: str
    gateway_ip: str
    channel: str


class ProtectionCheck(BaseModel):
    id: str
    label: str
    ok: bool
    detail: str


class ProtectionMetrics(BaseModel):
    protected_seconds: int
    protected_ratio: float
    contained_entries: int
    recoveries: int
    recovery_success_rate: float | None


class ProtectionStatus(BaseModel):
    status: Literal["protected", "contained", "degraded", "demo"]
    safe: bool
    label: str
    summary: str
    checks: list[ProtectionCheck]
    metrics: ProtectionMetrics


class Activity(BaseModel):
    id: int
    kind: str
    message: str
    created_at: int


class StatusResponse(BaseModel):
    device_name: str
    demo_mode: bool
    version: str
    system: SystemStatus
    tor: TorStatus
    network: NetworkStatus
    protection: ProtectionStatus
    activities: list[Activity]


class ConfigurationDocument(BaseModel):
    version: Literal[1]
    exported_at: int
    device_name: str
    tor_policy: dict[str, Any]
    dns_filter: dict[str, Any]
    blocked_devices: list[dict[str, Any]]
    # Added in 0.4.0. Optional so a backup taken by an older release still
    # restores instead of being rejected as malformed.
    device_access: list[dict[str, Any]] = []
    circumvention: dict[str, Any]
