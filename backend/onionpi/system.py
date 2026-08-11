from __future__ import annotations

import random
import re
import socket
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

import psutil


def _run(arguments: list[str], timeout: float = 3) -> str:
    try:
        result = subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"},
        )
        return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


class MetricsSampler:
    def __init__(self, interface: str, demo_mode: bool = False) -> None:
        self.interface = interface
        self.demo_mode = demo_mode
        self._history: deque[dict[str, Any]] = deque(maxlen=180)
        self._last: tuple[float, int, int] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="onionpi-metrics", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.sample()
            self._stop.wait(5)

    def sample(self) -> None:
        now = time.time()
        counters = psutil.net_io_counters(pernic=True)
        counter = counters.get(self.interface) or psutil.net_io_counters()
        sent, received = int(counter.bytes_sent), int(counter.bytes_recv)
        if self._last is None:
            down = up = 0.0
        else:
            last_time, last_sent, last_received = self._last
            elapsed = max(now - last_time, 0.001)
            up = max(0.0, (sent - last_sent) * 8 / elapsed / 1_000_000)
            down = max(0.0, (received - last_received) * 8 / elapsed / 1_000_000)
        self._last = (now, sent, received)
        if self.demo_mode:
            position = len(self._history)
            down = max(0.4, 4.6 + 1.8 * random.random() + 1.1 * (position % 8 == 0))
            up = max(0.2, 1.2 + 1.1 * random.random())
        with self._lock:
            self._history.append(
                {"timestamp": int(now), "download_mbps": round(down, 2), "upload_mbps": round(up, 2)}
            )

    def history(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._history)


def _temperature() -> float | None:
    thermal_path = Path("/sys/class/thermal/thermal_zone0/temp")
    try:
        return round(float(thermal_path.read_text().strip()) / 1000, 1)
    except (OSError, ValueError):
        try:
            temperatures = psutil.sensors_temperatures()
            first = next(iter(temperatures.values()))[0]
            return round(float(first.current), 1)
        except (AttributeError, StopIteration, IndexError):
            return None


def service_states(demo_mode: bool = False) -> list[dict[str, Any]]:
    services = [
        ("tor", "Tor"),
        ("NetworkManager", "Wi-Fi"),
        ("dnsmasq", "DNS"),
        ("onionpi-firewall", "Pare-feu"),
    ]
    if demo_mode:
        return [{"id": service, "label": label, "active": True} for service, label in services]
    return [
        {
            "id": service,
            "label": label,
            "active": _run(["systemctl", "is-active", service]) == "active",
        }
        for service, label in services
    ]


def system_snapshot(shared_dir: Path, demo_mode: bool = False) -> dict[str, Any]:
    usage = psutil.disk_usage(str(shared_dir))
    memory = psutil.virtual_memory()
    boot_time = psutil.boot_time()
    return {
        "hostname": socket.gethostname(),
        "cpu_percent": 34.0 if demo_mode else round(psutil.cpu_percent(interval=0.1), 1),
        "memory_percent": 48.0 if demo_mode else round(memory.percent, 1),
        "temperature_c": 52.0 if demo_mode else _temperature(),
        "storage_percent": 24.0 if demo_mode else round(usage.percent, 1),
        "storage_used": 9_400_000_000 if demo_mode else usage.used,
        "storage_total": 38_600_000_000 if demo_mode else usage.total,
        "uptime_seconds": max(0, int(time.time() - boot_time)),
        "services": service_states(demo_mode),
    }


def connected_devices(interface: str, demo_mode: bool = False) -> list[dict[str, Any]]:
    if demo_mode:
        return [
            # Full addresses: the demo interface offers the same block button
            # as a real install, and that button needs a valid MAC.
            {"name": "MacBook Pro", "ip": "10.42.0.10", "mac": "6a:4f:12:8b:33:21", "download": 1_230_000_000, "upload": 320_000_000, "online": True},
            {"name": "iPhone", "ip": "10.42.0.11", "mac": "3c:07:54:2a:91:8f", "download": 860_000_000, "upload": 210_000_000, "online": True},
            {"name": "Raspberry Pi", "ip": "10.42.0.1", "mac": "dc:a6:32:1f:70:45", "download": 420_000_000, "upload": 95_000_000, "online": True},
            {"name": "Desktop PC", "ip": "10.42.0.12", "mac": "8e:2b:66:d4:05:19", "download": 310_000_000, "upload": 80_000_000, "online": True},
        ]
    neighbor_output = _run(["ip", "neighbor", "show", "dev", interface])
    leases: dict[str, str] = {}
    try:
        for line in Path("/var/lib/misc/dnsmasq.leases").read_text().splitlines():
            parts = line.split()
            if len(parts) >= 4:
                leases[parts[2].lower()] = parts[3] if parts[3] != "*" else "Appareil"
    except OSError:
        pass
    devices: list[dict[str, Any]] = []
    for line in neighbor_output.splitlines():
        match = re.match(r"(\S+).*lladdr\s+([0-9a-f:]{17})\s+(\S+)", line, re.I)
        if not match:
            continue
        ip_address, mac, state = match.groups()
        devices.append(
            {
                "name": leases.get(ip_address, "Appareil"),
                "ip": ip_address,
                "mac": mac.lower(),
                "download": 0,
                "upload": 0,
                "online": state.upper() not in {"FAILED", "INCOMPLETE"},
            }
        )
    return devices


def wifi_details(interface: str, upstream: str, gateway_ip: str, demo_mode: bool) -> dict[str, Any]:
    if demo_mode:
        return {
            "ssid": "OnionPi Wi-Fi",
            "wifi_interface": interface,
            "upstream_interface": upstream,
            "gateway_ip": gateway_ip,
            "channel": "7",
        }
    ssid = _run(["nmcli", "-g", "802-11-wireless.ssid", "connection", "show", "onionpi-ap"])
    channel = _run(["nmcli", "-g", "802-11-wireless.channel", "connection", "show", "onionpi-ap"])
    return {
        "ssid": ssid or "OnionPi",
        "wifi_interface": interface,
        "upstream_interface": upstream,
        "gateway_ip": gateway_ip,
        "channel": channel or "auto",
    }


def journal(service: str, limit: int = 100) -> list[str]:
    allowed = {
        "tor",
        "NetworkManager",
        "dnsmasq",
        "nftables",
        "onionpi-firewall",
        "onionpi",
        "snowflake-proxy",
    }
    if service not in allowed:
        return []
    output = _run(
        ["journalctl", "-u", service, "--no-pager", "--output=short-iso", "-n", str(min(limit, 250))],
        timeout=5,
    )
    return output.splitlines() if output else []
