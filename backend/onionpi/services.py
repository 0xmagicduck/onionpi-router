"""Composition root for the application and its replaceable platform adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .access import DeviceAccessManager
from .accounting import TrafficAccountant
from .agent import PrivilegedAgent
from .auth import LoginLimiter, RateLimiter
from .backends import (
    AccessPointBackend,
    DemoAccessPointBackend,
    DemoFirewallBackend,
    FirewallBackend,
    RaspberryPiAccessPointBackend,
    RaspberryPiFirewallBackend,
)
from .circumvention import CircumventionManager
from .config import Settings
from .database import Database
from .maintenance import MaintenanceWindow
from .mesh import MeshCoordinator
from .netcontrol import DeviceGuard, DnsFilter
from .nodeclient import NodeClient
from .onboarding import OnboardingManager
from .onion import OnionService
from .policy import TorPolicy
from .rack import RackManager
from .relay import SnowflakeRelay
from .system import MetricsSampler, connected_devices
from .tor_control import TorController
from .updates import UpdateManager


@dataclass(slots=True)
class AppServices:
    settings: Settings
    database: Database
    tor: TorController
    metrics: MetricsSampler
    login_limiter: LoginLimiter
    circumvention: CircumventionManager
    relay: SnowflakeRelay
    agent: PrivilegedAgent
    device_guard: DeviceGuard
    access: DeviceAccessManager
    traffic: TrafficAccountant
    dns_filter: DnsFilter
    tor_policy: TorPolicy
    onion: OnionService
    updates: UpdateManager
    speedtest_limiter: RateLimiter
    firewall: FirewallBackend
    access_point: AccessPointBackend
    maintenance: MaintenanceWindow
    onboarding: OnboardingManager
    rack: RackManager


def build_app_services(settings: Settings) -> AppServices:
    database = Database(settings.database_path)
    tor = TorController(
        settings.tor_control_host,
        settings.tor_control_port,
        settings.tor_cookie_path,
        settings.demo_mode,
    )
    metrics = MetricsSampler(settings.upstream_interface, settings.demo_mode)
    login_limiter = LoginLimiter()
    circumvention = CircumventionManager(
        config_path=settings.bridge_config_path,
        state_path=settings.circumvention_state_path,
        cache_path=settings.circumvention_cache_path,
        controller=tor,
        country=settings.country,
        demo_mode=settings.demo_mode,
        on_event=lambda kind, message: database.add_activity(kind, message),
    )
    relay = SnowflakeRelay(settings.relay_state_path, settings.demo_mode)
    agent = PrivilegedAgent(
        settings.agent_request_path, settings.agent_result_path, settings.demo_mode
    )
    device_guard = DeviceGuard(
        database,
        settings.blocked_macs_path,
        agent,
        settings.demo_mode,
        on_event=lambda kind, message: database.add_activity(kind, message),
    )
    access = DeviceAccessManager(
        database,
        device_guard,
        on_event=lambda kind, message: database.add_activity(kind, message),
    )
    traffic = TrafficAccountant(
        database,
        settings.traffic_state_path,
        settings.demo_mode,
    )
    dns_filter = DnsFilter(
        database,
        settings.dns_block_path,
        agent,
        socks_port=settings.tor_socks_port,
        demo_mode=settings.demo_mode,
        on_event=lambda kind, message: database.add_activity(kind, message),
    )
    def wifi_view() -> list[dict[str, Any]]:
        """The Wi-Fi as the rack reads it: leases plus what they moved.

        `totals` rather than `update`: reading the page must not fold the
        firewall counters, which is the devices endpoint's job and its alone.
        """
        totals = traffic.totals()
        return [
            {**device, **totals.get(str(device.get("mac", "")), {})}
            for device in connected_devices(settings.wifi_interface, settings.demo_mode)
        ]

    rack = RackManager(
        database,
        settings.rack_key_path,
        device_guard,
        access,
        NodeClient(settings.tor_socks_port, settings.demo_mode),
        tor,
        MeshCoordinator(settings.mesh_key_path, database, settings.demo_mode),
        demo_mode=settings.demo_mode,
        on_event=lambda kind, message: database.add_activity(kind, message),
        wifi_view=wifi_view,
        agent_dir=settings.node_agent_dir,
        source_ref=settings.source_ref,
    )
    tor_policy = TorPolicy(
        database,
        settings.tor_policy_path,
        tor,
        settings.demo_mode,
        on_event=lambda kind, message: database.add_activity(kind, message),
    )
    onion = OnionService(
        database,
        settings.onion_key_path,
        tor,
        target=f"127.0.0.1:{settings.onion_target_port}",
        demo_mode=settings.demo_mode,
        on_event=lambda kind, message: database.add_activity(kind, message),
    )
    updates = UpdateManager(
        settings.update_state_path,
        settings.update_settings_path,
        settings.version,
        settings.demo_mode,
    )
    if settings.demo_mode:
        firewall: FirewallBackend = DemoFirewallBackend()
        access_point: AccessPointBackend = DemoAccessPointBackend(
            settings.wifi_interface,
            settings.upstream_interface,
            settings.gateway_ip,
            settings.mesh_interface,
            settings.mesh_device,
            settings.mesh_id,
            settings.mesh_address,
        )
    else:
        firewall = RaspberryPiFirewallBackend(agent)
        access_point = RaspberryPiAccessPointBackend(
            agent,
            settings.wifi_interface,
            settings.upstream_interface,
            settings.gateway_ip,
            settings.mesh_interface,
            settings.mesh_device,
            settings.mesh_id,
            settings.mesh_address,
        )
    maintenance = MaintenanceWindow(settings.maintenance_state_path, settings.demo_mode)
    onboarding = OnboardingManager(database, settings, firewall, maintenance)
    return AppServices(
        settings=settings,
        database=database,
        tor=tor,
        metrics=metrics,
        login_limiter=login_limiter,
        circumvention=circumvention,
        relay=relay,
        agent=agent,
        device_guard=device_guard,
        access=access,
        traffic=traffic,
        dns_filter=dns_filter,
        tor_policy=tor_policy,
        onion=onion,
        updates=updates,
        speedtest_limiter=RateLimiter(events=3, window_seconds=120),
        firewall=firewall,
        access_point=access_point,
        maintenance=maintenance,
        onboarding=onboarding,
        rack=rack,
    )
