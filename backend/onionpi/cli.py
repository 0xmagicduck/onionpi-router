from __future__ import annotations

import argparse
import getpass
import os
import re
import secrets
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .auth import hash_password
from .config import get_settings
from .database import Database
from .mesh import (
    IDENTITY_PREFIX,
    encode_key,
    endorsement_message,
)


def main() -> None:
    parser = argparse.ArgumentParser(prog="onionpi-admin")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create-admin", help="Créer ou remplacer le compte administrateur")
    create.add_argument("--username", default="admin")
    create.add_argument("--display-name", default="Administrateur")
    create.add_argument("--password-stdin", action="store_true")
    create.add_argument(
        "--password-hash-stdin",
        action="store_true",
        help="lire un condensat scrypt déjà calculé, pour ne jamais écrire le mot de passe en clair",
    )
    trustee = subparsers.add_parser(
        "mesh-trustee",
        help="Créer une clé de garant du verrou de maillage",
    )
    trustee.add_argument(
        "--out",
        required=True,
        help="fichier où écrire la clé privée du garant, créé en 0600",
    )
    endorse = subparsers.add_parser(
        "mesh-endorse",
        help="Contresigner la clé de maillage d’un nœud, en tant que garant",
    )
    endorse.add_argument("--key", required=True, help="fichier de clé du garant")
    endorse.add_argument("--node", required=True, help="identifiant du nœud")
    endorse.add_argument("--identity", required=True, help="clé « ed25519:… » du nœud")
    arguments = parser.parse_args()

    if arguments.command == "mesh-trustee":
        mesh_trustee(parser, arguments.out)
        return
    if arguments.command == "mesh-endorse":
        mesh_endorse(parser, arguments.key, arguments.node, arguments.identity)
        return

    if not re.fullmatch(r"[a-zA-Z0-9_.-]{3,32}", arguments.username):
        parser.error("Nom d’utilisateur invalide")
    if arguments.password_hash_stdin:
        password_hash = sys.stdin.readline().strip()
        if not re.fullmatch(r"scrypt\$\d+\$\d+\$\d+\$[\w=-]+\$[\w=-]+", password_hash):
            parser.error("Condensat scrypt invalide")
    else:
        password = sys.stdin.readline().rstrip("\n") if arguments.password_stdin else getpass.getpass(
            "Mot de passe administrateur: "
        )
        password_hash = hash_password(password)
    settings = get_settings()
    database = Database(settings.database_path)
    database.initialize()
    database.create_user(
        arguments.username,
        arguments.display_name.strip()[:80] or arguments.username,
        password_hash,
    )
    print(f"Compte {arguments.username} prêt.")


def mesh_trustee(parser: argparse.ArgumentParser, destination: str) -> None:
    """Crée la clé d'un garant du verrou de maillage.

    Elle n'a rien à faire sur l'appliance: le verrou existe précisément pour que
    la compromission du coordinateur n'ouvre pas le maillage. Rangez-la
    ailleurs, sur une machine différente de chaque autre garant.
    """
    path = Path(destination)
    if path.exists():
        parser.error(f"{path} existe déjà: une clé de garant ne se remplace pas en silence")
    seed = secrets.token_bytes(32)
    private = Ed25519PrivateKey.from_private_bytes(seed)
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    handle = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        stream.write(seed.hex() + "\n")
    print(f"Clé de garant écrite dans {path} (0600).")
    print(f"Clé publique à déclarer dans « Maillage → Verrou »:\n{encode_key(IDENTITY_PREFIX, public)}")


def mesh_endorse(
    parser: argparse.ArgumentParser, key_path: str, node: str, identity: str
) -> None:
    """Signe « ce nœud a bien cette clé », en tant que garant."""
    if not re.fullmatch(r"[0-9a-f]{16}", node):
        parser.error("Identifiant de nœud invalide")
    if not re.fullmatch(r"ed25519:[0-9a-f]{64}", identity):
        parser.error("Clé de nœud invalide: « ed25519: » et 64 caractères hexadécimaux")
    try:
        seed = bytes.fromhex(Path(key_path).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        parser.error(f"Clé de garant illisible: {key_path}")
    if len(seed) != 32:
        parser.error("Clé de garant invalide")
    private = Ed25519PrivateKey.from_private_bytes(seed)
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    signature = private.sign(endorsement_message(node, identity)).hex()
    print(f"Garant   : {encode_key(IDENTITY_PREFIX, public)}")
    print(f"Signature: {signature}")


if __name__ == "__main__":
    main()
