#!/usr/bin/env python3
"""Traduit la politique d'un nœud en jeu de règles nftables. Exécuté sous root.

`onionpi-node-apply.sh` ne lit jamais le JSON lui-même: il appelle ce programme,
qui revalide chaque champ avant d'écrire une seule ligne de règle. C'est ici que
se trouve la validation qui compte — l'agent, non privilégié, ne fait que
proposer un document.

Le pare-feu produit tient en trois idées:

* sortie interdite par défaut, sauf le trafic du démon Tor lui-même. Une
  application qui ignore le proxy n'atteint rien: elle échoue, elle ne fuit pas;
* entrée interdite par défaut, sauf les ports que l'opérateur garde ouverts.
  Le port 22 en fait partie par défaut, parce qu'un VPS injoignable est un VPS
  perdu;
* isolement: les applications perdent aussi l'accès au port SOCKS de Tor. La
  machine reste administrable et son service onion reste publié, mais elle ne
  sort plus.
"""

from __future__ import annotations

import json
import pwd
import re
import sys
from pathlib import Path
from typing import NoReturn

TABLE = "onionpi_node"
EGRESS_MODES = ("tor-only", "direct")
MAX_PORTS = 8

#: Version du document accepté. La 2 ajoute les deux champs du maillage; un
#: document de version 1 n'arrive plus, la baie et l'agent étant publiés
#: ensemble, et le refuser vaut mieux que deviner ce que ses champs voulaient.
POLICY_VERSION = 2

#: Le plan d'adressage du lien radio `bat0`, tel que `packaging/install.sh` le
#: pose. Écrit ici plutôt que reçu: c'est le seul réseau vers lequel le
#: coupe-circuit ouvre une exception, et une exception dont l'étendue arrive
#: dans un fichier n'est plus une exception bornée.
MESH_NETWORK = "10.43.0.0/16"

#: Nom du compte du démon Tor selon la distribution.
TOR_ACCOUNTS = ("debian-tor", "tor", "_tor")
AGENT_ACCOUNT = "onionpi-node"


def fail(message: str) -> NoReturn:
    print(f"politique refusée: {message}", file=sys.stderr)
    raise SystemExit(2)


def account_id(names: tuple[str, ...] | str) -> int | None:
    for name in (names,) if isinstance(names, str) else names:
        try:
            return pwd.getpwnam(name).pw_uid
        except KeyError:
            continue
    return None


def load(path: Path) -> dict[str, object]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        fail(f"fichier illisible ({error})")
    except json.JSONDecodeError as error:
        fail(f"JSON invalide ({error})")
    if not isinstance(document, dict):
        fail("document attendu: un objet")
    if document.get("version") != POLICY_VERSION:
        fail("version de politique inconnue")
    egress = document.get("egress")
    if egress not in EGRESS_MODES:
        fail("mode de sortie inconnu")
    country = document.get("exit_country", "")
    if not isinstance(country, str) or (country and not re.fullmatch(r"[A-Z]{2}", country)):
        fail("pays de sortie invalide")
    raw_ports = document.get("keep_open_ports", [])
    if not isinstance(raw_ports, list) or len(raw_ports) > MAX_PORTS:
        fail("liste de ports invalide")
    ports = []
    for port in raw_ports:
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            fail("port invalide")
        if port not in ports:
            ports.append(port)
    digest = document.get("digest", "")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        fail("empreinte invalide")
    mesh_port = document.get("mesh_port", 0)
    if not isinstance(mesh_port, int) or isinstance(mesh_port, bool) or not 0 <= mesh_port <= 65535:
        fail("port de maillage invalide")
    mesh_enabled = bool(document.get("mesh_enabled", False)) and mesh_port > 0
    return {
        "egress": egress,
        "exit_country": country,
        "keep_open_ports": sorted(ports),
        "isolated": bool(document.get("isolated", False)),
        "mesh_enabled": mesh_enabled,
        "mesh_port": mesh_port if mesh_enabled else 0,
        "digest": digest,
    }


def render(policy: dict[str, object], socks_port: int) -> str:
    tor_uid = account_id(TOR_ACCOUNTS)
    agent_uid = account_id(AGENT_ACCOUNT)
    if tor_uid is None:
        fail("compte du démon Tor introuvable: Tor n'est pas installé")
    ports = policy["keep_open_ports"] if isinstance(policy["keep_open_ports"], list) else []
    lines = [
        f"table inet {TABLE} {{",
        "  chain input {",
        "    type filter hook input priority 0; policy drop;",
        "    ct state invalid drop",
        "    ct state established,related accept",
        '    iif "lo" accept',
        "    ip protocol icmp accept",
        "    ip6 nexthdr icmpv6 accept",
    ]
    if ports:
        listed = ", ".join(str(port) for port in ports)
        lines.append(f"    tcp dport {{ {listed} }} accept")
    if policy["mesh_enabled"]:
        # Le chemin direct du maillage: un pair à portée radio joint ce nœud
        # sur `bat0` sans payer six sauts Tor. Un seul port, un seul réseau, et
        # ce qui passe dedans est du Noise — le lien n'est pas une frontière de
        # confiance, la poignée de main l'est.
        lines.append(
            f"    ip saddr {MESH_NETWORK} tcp dport {policy['mesh_port']} accept"
        )
    lines += [
        "  }",
        "  chain forward {",
        "    type filter hook forward priority 0; policy drop;",
        "  }",
        "  chain output {",
    ]
    if policy["egress"] == "direct":
        # Le nœud reste inventorié et surveillé, mais rien ne le force à sortir
        # par Tor. C'est une dérogation, pas le mode normal.
        lines += [
            "    type filter hook output priority 0; policy accept;",
            "  }",
            "}",
        ]
        return "\n".join(lines) + "\n"
    lines += [
        "    type filter hook output priority 0; policy drop;",
        "    ct state invalid drop",
    ]
    if policy["isolated"]:
        # Avant la règle qui accepte la boucle locale: isoler, c'est retirer aux
        # applications l'accès au proxy, pas éteindre l'agent ni le service onion.
        allowed = [uid for uid in (tor_uid, agent_uid, 0) if uid is not None]
        skuid = ", ".join(str(uid) for uid in sorted(set(allowed)))
        lines.append(
            f'    oif "lo" tcp dport {socks_port} meta skuid != {{ {skuid} }} drop'
        )
    lines += [
        "    ct state established,related accept",
        '    oif "lo" accept',
        f"    meta skuid {tor_uid} accept",
    ]
    if policy["mesh_enabled"] and not policy["isolated"]:
        # L'autre moitié du chemin direct. Retirée quand le nœud est isolé:
        # isoler veut dire « il ne sort plus », et un pair du maillage reste une
        # sortie. Le chemin relayé par Tor, lui, n'a besoin d'aucune exception.
        lines.append(
            f"    ip daddr {MESH_NETWORK} tcp dport {policy['mesh_port']} accept"
        )
    lines += ["  }", "}"]
    return "\n".join(lines) + "\n"


def main() -> None:
    if len(sys.argv) != 3:
        fail("usage: render-policy.py <politique.json> <sortie.nft>")
    policy = load(Path(sys.argv[1]))
    socks_port = 9050
    try:
        torrc = Path("/etc/tor/torrc").read_text(encoding="utf-8")
    except OSError:
        torrc = ""
    match = re.search(r"^\s*SocksPort\s+(?:127\.0\.0\.1:)?(\d{1,5})", torrc, re.MULTILINE)
    if match and 1 <= int(match.group(1)) <= 65535:
        socks_port = int(match.group(1))
    Path(sys.argv[2]).write_text(render(policy, socks_port), encoding="utf-8")
    print(policy["digest"])


if __name__ == "__main__":
    main()
