from __future__ import annotations

import json
import os
import random
import re
import shutil
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
        if self.demo_mode:
            position = len(self._history)
            down = max(0.4, 4.6 + 1.8 * random.random() + 1.1 * (position % 8 == 0))
            up = max(0.2, 1.2 + 1.1 * random.random())
        else:
            try:
                counters = psutil.net_io_counters(pernic=True)
                counter = counters.get(self.interface) or psutil.net_io_counters()
                if counter is None:
                    raise OSError("compteurs réseau indisponibles")
                sent, received = int(counter.bytes_sent), int(counter.bytes_recv)
            except (OSError, psutil.Error):
                # A restricted host must not kill the sampler thread forever.
                self._last = None
                down = up = 0.0
            else:
                if self._last is None:
                    down = up = 0.0
                else:
                    last_time, last_sent, last_received = self._last
                    elapsed = max(now - last_time, 0.001)
                    up = max(0.0, (sent - last_sent) * 8 / elapsed / 1_000_000)
                    down = max(0.0, (received - last_received) * 8 / elapsed / 1_000_000)
                self._last = (now, sent, received)
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
        except (AttributeError, StopIteration, IndexError, OSError, psutil.Error):
            return None


def service_states(demo_mode: bool = False) -> list[dict[str, Any]]:
    services = [
        ("tor", "Tor"),
        ("NetworkManager", "Wi-Fi"),
        ("onionpi-ap", "Point d’accès"),
        ("dnsmasq", "DNS"),
        ("onionpi-firewall", "Pare-feu"),
    ]
    if os.environ.get("ONIONPI_MESH_INTERFACE", "").strip():
        services.append(("onionpi-mesh", "Maillage"))
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
    if demo_mode:
        return {
            "hostname": "onionpi",
            "cpu_percent": 34.0,
            "memory_percent": 48.0,
            "temperature_c": 52.0,
            "storage_percent": 24.0,
            "storage_used": 9_400_000_000,
            "storage_total": 38_600_000_000,
            "uptime_seconds": 243_600,
            "services": service_states(True),
        }

    try:
        usage = psutil.disk_usage(str(shared_dir))
        storage_percent = round(usage.percent, 1)
        storage_used = usage.used
        storage_total = usage.total
    except (OSError, psutil.Error):
        try:
            fallback = shutil.disk_usage(shared_dir)
            storage_used = fallback.used
            storage_total = fallback.total
            storage_percent = round((fallback.used / fallback.total) * 100, 1)
        except (OSError, ZeroDivisionError):
            storage_percent = 0.0
            storage_used = storage_total = 0

    try:
        memory_percent = round(psutil.virtual_memory().percent, 1)
    except (OSError, psutil.Error):
        memory_percent = 0.0
    try:
        boot_time = psutil.boot_time()
        uptime_seconds = max(0, int(time.time() - boot_time))
    except (OSError, psutil.Error):
        uptime_seconds = 0
    try:
        cpu_percent = round(psutil.cpu_percent(interval=0.1), 1)
    except (OSError, psutil.Error):
        cpu_percent = 0.0
    try:
        hostname = socket.gethostname()
    except OSError:
        hostname = "onionpi"
    return {
        "hostname": hostname,
        "cpu_percent": cpu_percent,
        "memory_percent": memory_percent,
        "temperature_c": _temperature(),
        "storage_percent": storage_percent,
        "storage_used": storage_used,
        "storage_total": storage_total,
        "uptime_seconds": uptime_seconds,
        "services": service_states(False),
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


def _mesh_peers(mesh_device: str) -> list[dict[str, Any]]:
    """Reads batman-adv originators without making their text format an API.

    batctl's columns have changed names across Raspberry Pi OS releases, while
    the useful row shape has stayed stable: originator, age, optional link
    throughput, next hop and hard interface. Unknown columns are deliberately
    ignored so a diagnostic upgrade cannot break /status.
    """
    output = _run(["batctl", "-m", mesh_device, "originators"], timeout=4)
    peers: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in output.splitlines():
        macs = re.findall(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}", line, re.I)
        if not macs:
            continue
        originator = macs[0].lower()
        if originator in seen:
            continue
        seen.add(originator)
        age = re.search(r"\b(?:\d+\.\d+|\d+)s\b", line)
        # B.A.T.M.A.N. V prints the estimated MBit/s in parentheses. Ignore
        # the TQ value used by older algorithms: it has no bandwidth unit.
        throughput = re.search(r"\(\s*(\d+(?:\.\d+)?)\s*MBit\)", line, re.I)
        peers.append(
            {
                "mac": originator,
                "last_seen": age.group(0) if age else "inconnu",
                "throughput_mbps": float(throughput.group(1)) if throughput else None,
                "next_hop": macs[1].lower() if len(macs) > 1 else originator,
            }
        )
    return peers[:128]


def mesh_details(
    radio_interface: str,
    mesh_device: str,
    mesh_id: str,
    mesh_address: str,
    demo_mode: bool,
) -> dict[str, Any]:
    enabled = bool(radio_interface)
    if not enabled:
        return {
            "enabled": False,
            "active": False,
            "mesh_id": "",
            "radio_interface": "",
            "interface": mesh_device,
            "address": "",
            "peers": [],
            "peer_count": 0,
        }
    if demo_mode:
        peers = [
            {
                "mac": "02:42:ac:11:00:02",
                "last_seen": "0.4s",
                "throughput_mbps": 144.4,
                "next_hop": "02:42:ac:11:00:02",
            },
            {
                "mac": "02:42:ac:11:00:03",
                "last_seen": "0.8s",
                "throughput_mbps": 72.2,
                "next_hop": "02:42:ac:11:00:02",
            },
        ]
        return {
            "enabled": True,
            "active": True,
            "mesh_id": mesh_id or "OnionPi-Mesh",
            "radio_interface": radio_interface,
            "interface": mesh_device,
            "address": mesh_address or "10.43.0.1/16",
            "peers": peers,
            "peer_count": len(peers),
        }
    raw_link = _run(["ip", "-j", "link", "show", "dev", mesh_device])
    try:
        link = json.loads(raw_link)[0]
        active = "UP" in link.get("flags", []) and str(link.get("operstate", "")) != "DOWN"
    except (json.JSONDecodeError, IndexError, KeyError, TypeError):
        active = False
    peers = _mesh_peers(mesh_device) if active else []
    return {
        "enabled": True,
        "active": active,
        "mesh_id": mesh_id,
        "radio_interface": radio_interface,
        "interface": mesh_device,
        "address": mesh_address,
        "peers": peers,
        "peer_count": len(peers),
    }


def wifi_details(
    interface: str,
    upstream: str,
    gateway_ip: str,
    demo_mode: bool,
    mesh_interface: str = "",
    mesh_device: str = "bat0",
    mesh_id: str = "",
    mesh_address: str = "",
) -> dict[str, Any]:
    mesh = mesh_details(mesh_interface, mesh_device, mesh_id, mesh_address, demo_mode)
    if demo_mode:
        return {
            "ssid": "OnionPi Wi-Fi",
            "wifi_interface": interface,
            "upstream_interface": upstream,
            "gateway_ip": gateway_ip,
            "channel": "7",
            "mesh": mesh,
        }
    ssid = _run(["nmcli", "-g", "802-11-wireless.ssid", "connection", "show", "onionpi-ap"])
    channel = _run(["nmcli", "-g", "802-11-wireless.channel", "connection", "show", "onionpi-ap"])
    return {
        "ssid": ssid or "OnionPi",
        "wifi_interface": interface,
        "upstream_interface": upstream,
        "gateway_ip": gateway_ip,
        "channel": channel or "auto",
        "mesh": mesh,
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
