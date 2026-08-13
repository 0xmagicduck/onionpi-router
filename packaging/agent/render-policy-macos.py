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


def fail(message: str) -> NoReturn:
    print(f"politique refusée: {message}", file=sys.stderr)
    raise SystemExit(2)


def load(path: Path) -> dict[str, object]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"document illisible ({error})")
    if not isinstance(document, dict) or document.get("version") != 1:
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


def render(policy: dict[str, object]) -> str:
    if policy["egress"] == "direct":
        return ""
    ports = policy["keep_open_ports"]
    lines = [
        "pass quick on lo0 all" if not policy["isolated"] else "block return quick on lo0 proto tcp to port 9050 user != _onionpi-node",
        "block return out all",
        f"pass out quick inet proto {{ tcp, udp }} user {SERVICE_USER} keep state",
        f"pass out quick inet6 proto {{ tcp, udp }} user {SERVICE_USER} keep state",
    ]
    if policy["isolated"]:
        lines.append("pass quick on lo0 all")
    if isinstance(ports, list) and ports:
        listed = ", ".join(str(port) for port in ports)
        lines += [
            f"pass in quick proto tcp to port {{ {listed} }} keep state",
            f"pass out quick proto tcp from port {{ {listed} }} keep state",
        ]
    return "\n".join(lines) + "\n"


def main() -> None:
    if len(sys.argv) != 3:
        fail("usage: render-policy-macos.py <politique.json> <sortie.pf>")
    policy = load(Path(sys.argv[1]))
    Path(sys.argv[2]).write_text(render(policy), encoding="utf-8")
    print(f"{policy['digest']} {policy['egress']}")


if __name__ == "__main__":
    main()
