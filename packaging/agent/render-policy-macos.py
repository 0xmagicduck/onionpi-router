#!/usr/bin/env python3
"""Valide une politique OnionPi et produit l'ancre PF équivalente."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import NoReturn

SERVICE_USER = "_onionpi-node"
MAX_PORTS = 8
#: Même version que `render-policy.py`. Les deux champs du maillage qu'elle
#: ajoute concernent le chemin direct sur `bat0`, qui n'existe pas ici: un Mac
#: n'a pas de radio 802.11s pilotée par OnionPi, et son maillage passe par Tor,
#: qui ne demande aucune exception au coupe-circuit.
POLICY_VERSION = 2


def fail(message: str) -> NoReturn:
    print(f"politique refusée: {message}", file=sys.stderr)
    raise SystemExit(2)


def load(path: Path) -> dict[str, object]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"document illisible ({error})")
    if not isinstance(document, dict) or document.get("version") != POLICY_VERSION:
        fail("version de politique inconnue")
    egress = document.get("egress")
    if egress not in {"tor-only", "direct"}:
        fail("mode de sortie inconnu")
    country = document.get("exit_country", "")
    if not isinstance(country, str) or (country and not re.fullmatch(r"[A-Z]{2}", country)):
        fail("pays de sortie invalide")
    raw_ports = document.get("keep_open_ports", [])
    if not isinstance(raw_ports, list) or len(raw_ports) > MAX_PORTS:
        fail("liste de ports invalide")
    ports: list[int] = []
    for port in raw_ports:
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            fail("port invalide")
        if port not in ports:
            ports.append(port)
    digest = document.get("digest", "")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        fail("empreinte invalide")
    return {
        "egress": egress,
        "keep_open_ports": sorted(ports),
        "isolated": bool(document.get("isolated", False)),
        "digest": digest,
    }


def render(policy: dict[str, object], socks_port: int, trans_port: int, dns_port: int) -> str:
    if policy["egress"] == "direct":
        return ""
    ports = policy["keep_open_ports"]
    local_networks = (
        "{ 0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10, 127.0.0.0/8, "
        "169.254.0.0/16, 172.16.0.0/12, 192.168.0.0/16, 224.0.0.0/4, 240.0.0.0/4 }"
    )
    loopback_rules = [
        (
            f"block return quick on lo0 proto tcp to port {socks_port} user != {SERVICE_USER}"
            if policy["isolated"]
            else ""
        ),
        "pass quick on lo0 all",
    ]
    lines = [
        f"table <onionpi_local4> const {local_networks}",
        "table <onionpi_tor_virtual4> const { 10.192.0.0/10 }",
        f"rdr on lo0 inet proto udp from any to any port 53 -> 127.0.0.1 port {dns_port}",
        f"rdr on lo0 inet proto tcp from any to any port 53 -> 127.0.0.1 port {trans_port}",
        (
            "rdr on lo0 inet proto tcp from any to <onionpi_tor_virtual4> "
            f"-> 127.0.0.1 port {trans_port}"
        ),
        f"rdr on lo0 inet proto tcp from any to ! <onionpi_local4> -> 127.0.0.1 port {trans_port}",
        *[rule for rule in loopback_rules if rule],
        "pass out quick inet proto udp from port 68 to 255.255.255.255 port 67 keep state",
        f"pass out quick inet proto {{ tcp, udp }} user {SERVICE_USER} keep state",
        f"pass out quick inet6 proto {{ tcp, udp }} user {SERVICE_USER} keep state",
    ]
    if not policy["isolated"]:
        lines += [
            (
                "pass out quick route-to (lo0 127.0.0.1) inet proto udp "
                f"from any to any port 53 user != {SERVICE_USER} keep state"
            ),
            (
                "pass out quick route-to (lo0 127.0.0.1) inet proto tcp "
                f"from any to any port 53 user != {SERVICE_USER} keep state"
            ),
            (
                "pass out quick route-to (lo0 127.0.0.1) inet proto tcp "
                f"from any to <onionpi_tor_virtual4> user != {SERVICE_USER} keep state"
            ),
            "pass out quick inet to <onionpi_local4> keep state",
            (
                "pass out quick route-to (lo0 127.0.0.1) inet proto tcp "
                f"from any to ! <onionpi_local4> user != {SERVICE_USER} keep state"
            ),
        ]
    lines.append("block return out all")
    if isinstance(ports, list) and ports:
        listed = ", ".join(str(port) for port in ports)
        lines += [
            f"pass in quick proto tcp to port {{ {listed} }} keep state",
            f"pass out quick proto tcp from port {{ {listed} }} keep state",
        ]
    return "\n".join(lines) + "\n"


def main() -> None:
    if len(sys.argv) != 6:
        fail(
            "usage: render-policy-macos.py <politique.json> <sortie.pf> "
            "<port-socks> <port-transparent> <port-dns>"
        )
    try:
        socks_port, trans_port, dns_port = map(int, sys.argv[3:6])
    except ValueError:
        fail("port Tor invalide")
    if any(not 1 <= port <= 65535 for port in (socks_port, trans_port, dns_port)):
        fail("port Tor invalide")
    if len({socks_port, trans_port, dns_port}) != 3:
        fail("les ports Tor doivent être distincts")
    policy = load(Path(sys.argv[1]))
    Path(sys.argv[2]).write_text(
        render(policy, socks_port, trans_port, dns_port), encoding="utf-8"
    )
    print(f"{policy['digest']} {policy['egress']}")


if __name__ == "__main__":
    main()
