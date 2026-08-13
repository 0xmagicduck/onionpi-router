"""Authenticated encrypted configuration backups with a dry-run preview."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from typing import Any

from .auth import hashing_slot

BACKUP_SCHEMA = "onionpi-config-backup-v1"

#: Same shape as the login digest, so one derivation holds 128 * N * r bytes —
#: 16 MiB with the parameters below.
KDF_N = 2**14
KDF_R = 8
KDF_P = 1


class BackupError(ValueError):
    pass


def _derive_key(passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    """Derives the envelope key under the process-wide scrypt memory cap.

    Every one of these allocates as much as a password verification, and the
    three backup endpoints are reachable by anyone holding a session. Without
    the shared slot a handful of parallel calls walk straight past the
    MemoryMax the service unit sets and the appliance loses its interface to
    the OOM killer.
    """
    with hashing_slot():
        return hashlib.scrypt(passphrase.encode(), salt=salt, n=n, r=r, p=p, dklen=32)


def _aesgcm() -> type[Any]:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as error:  # pragma: no cover - packaging guard
        raise RuntimeError("Le composant de chiffrement n’est pas installé") from error
    return AESGCM


def encrypt_configuration(document: dict[str, Any], passphrase: str) -> dict[str, Any]:
    if len(passphrase) < 12:
        raise BackupError("La phrase de sauvegarde doit contenir au moins 12 caractères")
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = _derive_key(passphrase, salt, KDF_N, KDF_R, KDF_P)
    plaintext = json.dumps(document, ensure_ascii=False, sort_keys=True).encode()
    ciphertext = _aesgcm()(key).encrypt(nonce, plaintext, BACKUP_SCHEMA.encode())
    return {
        "schema": BACKUP_SCHEMA,
        "created_at": int(time.time()),
        "kdf": {"name": "scrypt", "n": KDF_N, "r": KDF_R, "p": KDF_P, "salt": base64.b64encode(salt).decode()},
        "cipher": {"name": "aes-256-gcm", "nonce": base64.b64encode(nonce).decode()},
        "payload": base64.b64encode(ciphertext).decode(),
    }


def decrypt_configuration(envelope: dict[str, Any], passphrase: str) -> dict[str, Any]:
    try:
        from cryptography.exceptions import InvalidTag
    except ImportError as error:  # pragma: no cover - packaging guard
        raise RuntimeError("Le composant de chiffrement n’est pas installé") from error
    try:
        if envelope.get("schema") != BACKUP_SCHEMA:
            raise BackupError("Format de sauvegarde inconnu")
        kdf = envelope["kdf"]
        cipher = envelope["cipher"]
        if kdf.get("name") != "scrypt" or cipher.get("name") != "aes-256-gcm":
            raise BackupError("Algorithmes de sauvegarde non pris en charge")
        n, r, p = int(kdf["n"]), int(kdf["r"]), int(kdf["p"])
        if (n, r, p) != (KDF_N, KDF_R, KDF_P):
            raise BackupError("Paramètres de dérivation inattendus")
        salt = base64.b64decode(kdf["salt"], validate=True)
        nonce = base64.b64decode(cipher["nonce"], validate=True)
        payload = base64.b64decode(envelope["payload"], validate=True)
        if len(salt) != 16 or len(nonce) != 12 or len(payload) > 4 * 1024**2:
            raise BackupError("Sauvegarde invalide ou trop volumineuse")
        key = _derive_key(passphrase, salt, n, r, p)
        plaintext = _aesgcm()(key).decrypt(nonce, payload, BACKUP_SCHEMA.encode())
        document = json.loads(plaintext)
    except BackupError:
        raise
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, InvalidTag) as error:
        raise BackupError("Sauvegarde invalide ou phrase incorrecte") from error
    if not isinstance(document, dict):
        raise BackupError("Le contenu de la sauvegarde doit être un objet")
    return document


def configuration_diff(current: dict[str, Any], restored: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    keys = sorted((set(current) | set(restored)) - {"exported_at"})
    for key in keys:
        before = current.get(key)
        after = restored.get(key)
        if before != after:
            changes.append({"section": key, "before": before, "after": after})
    return changes
