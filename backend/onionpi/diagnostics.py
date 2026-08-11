"""Privacy-safe readiness diagnostics shown to the authenticated operator."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any

from .agent import PrivilegedAgent
from .config import Settings
from .database import Database
from .system import _run, service_states
from .tor_control import TorController


def _item(
    identifier: str,
    label: str,
    status: str,
    detail: str,
    remedy: str = "",
) -> dict[str, str]:
    return {
        "id": identifier,
        "label": label,
        "status": status,
        "detail": detail,
        "remedy": remedy,
    }


def _path_check(identifier: str, label: str, path: Path) -> dict[str, str]:
    if not path.exists():
        return _item(
            identifier,
            label,
            "error",
            f"{path} est absent.",
            "Relancez l’installateur OnionPi en mode mise à niveau.",
        )
    if not os.access(path, os.R_OK | os.W_OK):
        return _item(
            identifier,
            label,
            "error",
            f"{path} n’est pas accessible en lecture et écriture.",
            "Vérifiez le propriétaire et les permissions du fichier.",
        )
    return _item(identifier, label, "ok", f"{path} est accessible.")


def build_diagnostics(
    settings: Settings,
    database: Database,
    tor: TorController,
    agent: PrivilegedAgent,
) -> dict[str, Any]:
    checks: list[dict[str, str]] = []

    integrity = database.quick_check()
    checks.append(
        _item(
            "database",
            "Base locale",
            "ok" if integrity == "ok" else "error",
            "Intégrité SQLite vérifiée." if integrity == "ok" else f"SQLite signale : {integrity[:180]}",
            "Sauvegardez /var/lib/onionpi puis restaurez une base saine."
            if integrity != "ok"
            else "",
        )
    )

    try:
        if settings.demo_mode:
            free = 29_200_000_000
            percent = 24
        else:
            usage = shutil.disk_usage(settings.data_dir)
            free = usage.free
            percent = round((usage.used / usage.total) * 100) if usage.total else 100
    except OSError as error:
        free = 0
        percent = 100
        checks.append(
            _item("storage", "Stockage", "error", f"Lecture impossible : {error}")
        )
    else:
        if free <= settings.storage_reserve_bytes:
            level = "error"
            remedy = "Libérez de l’espace avant tout import ou mise à jour."
        elif percent >= 85 or free <= settings.storage_reserve_bytes * 2:
            level = "warning"
            remedy = "Supprimez les fichiers partagés devenus inutiles."
        else:
            level = "ok"
            remedy = ""
        checks.append(
            _item(
                "storage",
                "Stockage",
                level,
                f"{percent} % utilisé, {free / 1024**3:.1f} Gio disponibles.",
                remedy,
            )
        )

    services = service_states(settings.demo_mode)
    for service in services:
        active = bool(service["active"])
        checks.append(
            _item(
                f"service-{service['id']}",
                str(service["label"]),
                "ok" if active else "error",
                "Service actif." if active else "Service système arrêté.",
                f"Redémarrez {service['label']} depuis Maintenance ou la console."
                if not active
                else "",
            )
        )

    progress = tor.bootstrap_progress()
    checks.append(
        _item(
            "tor-bootstrap",
            "Circuit Tor",
            "ok" if progress >= 100 else "warning",
            f"Bootstrap à {progress} %.",
            "Ouvrez Contournement si la progression reste bloquée."
            if progress < 100
            else "",
        )
    )

    checks.append(
        _item(
            "agent",
            "Actions privilégiées",
            "ok" if agent.available else "error",
            "File de commandes disponible."
            if agent.available
            else "La file de commandes root est indisponible.",
            "Vérifiez onionpi-agent.path ou relancez l’installateur."
            if not agent.available
            else "",
        )
    )

    if not settings.demo_mode:
        checks.extend(
            (
                _path_check("bridges-file", "Configuration des ponts", settings.bridge_config_path),
                _path_check("policy-file", "Politique de sortie", settings.tor_policy_path),
                _path_check("dns-file", "Liste DNS", settings.dns_block_path),
            )
        )
        synchronised = _run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
        checks.append(
            _item(
                "clock",
                "Horloge système",
                "ok" if synchronised == "yes" else "warning",
                "Synchronisée par NTP."
                if synchronised == "yes"
                else "Synchronisation NTP non confirmée.",
                "Vérifiez systemd-timesyncd et la connexion amont."
                if synchronised != "yes"
                else "",
            )
        )

    levels = {check["status"] for check in checks}
    overall = "error" if "error" in levels else "warning" if "warning" in levels else "ok"
    return {
        "generated_at": int(time.time()),
        "status": overall,
        "checks": checks,
        "database": database.stats(),
    }
