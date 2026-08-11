"""Single source of truth for the protection state shown to operators."""

from __future__ import annotations

from typing import Any


def protection_snapshot(
    tor_state: dict[str, Any],
    services: list[dict[str, Any]],
    demo_mode: bool = False,
) -> dict[str, Any]:
    """Summarise whether traffic is protected, contained, or at risk.

    A connected Tor circuit alone is not enough to call the router protected:
    the firewall is the control that prevents a direct fallback when Tor or DNS
    fails. Keeping this decision in the backend prevents each screen from
    inventing a slightly different definition of "secure".
    """

    active = {str(item.get("id")): bool(item.get("active")) for item in services}
    checks = [
        {
            "id": "firewall",
            "label": "Coupe-circuit nftables",
            "ok": active.get("onionpi-firewall", False),
            "detail": "Bloque toute sortie directe des clients Wi-Fi.",
        },
        {
            "id": "tor",
            "label": "Circuit Tor",
            "ok": bool(tor_state.get("connected")),
            "detail": "Transporte le trafic TCP et les résolutions DNS.",
        },
        {
            "id": "dns",
            "label": "DNS local",
            "ok": active.get("dnsmasq", False),
            "detail": "N’utilise que le DNSPort de Tor comme résolveur amont.",
        },
        {
            "id": "access-point",
            "label": "Point d’accès Wi-Fi",
            "ok": active.get("onionpi-ap", False)
            and active.get("NetworkManager", False),
            "detail": "Présente le réseau OnionPi aux appareils clients.",
        },
    ]

    if demo_mode:
        return {
            "status": "demo",
            "safe": False,
            "label": "Démonstration",
            "summary": "État simulé : aucune protection réseau n’est appliquée.",
            "checks": checks,
        }

    firewall_active = active.get("onionpi-firewall", False)
    if not firewall_active:
        return {
            "status": "degraded",
            "safe": False,
            "label": "Protection dégradée",
            "summary": "Coupe-circuit arrêté : déconnectez les clients et rechargez le pare-feu.",
            "checks": checks,
        }

    unavailable = [check["label"] for check in checks[1:] if not check["ok"]]
    if unavailable:
        return {
            "status": "contained",
            "safe": True,
            "label": "Sorties bloquées",
            "summary": f"Le coupe-circuit protège les clients, mais {', '.join(unavailable)} est indisponible.",
            "checks": checks,
        }

    return {
        "status": "protected",
        "safe": True,
        "label": "Protégé",
        "summary": "Tor, le DNS et le coupe-circuit protègent le trafic des clients.",
        "checks": checks,
    }
