from __future__ import annotations

import argparse
import getpass
import re
import sys

from .auth import hash_password
from .config import get_settings
from .database import Database


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
    arguments = parser.parse_args()

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


if __name__ == "__main__":
    main()
