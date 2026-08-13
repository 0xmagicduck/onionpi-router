from __future__ import annotations

import threading

import pytest
from onionpi import auth
from onionpi.backup import (
    BackupError,
    configuration_diff,
    decrypt_configuration,
    encrypt_configuration,
)

PHRASE = "une phrase de sauvegarde solide"


def test_encrypted_backup_round_trip_and_wrong_phrase() -> None:
    document = {
        "version": 1,
        "exported_at": 1_700_000_000,
        "dns_filter": {"profiles": ["family"]},
    }
    envelope = encrypt_configuration(document, "une phrase de sauvegarde solide")

    assert envelope["schema"] == "onionpi-config-backup-v1"
    assert "family" not in envelope["payload"]
    assert decrypt_configuration(envelope, "une phrase de sauvegarde solide") == document
    with pytest.raises(BackupError):
        decrypt_configuration(envelope, "une autre phrase incorrecte")


def test_backup_parameters_and_payload_are_bounded() -> None:
    envelope = encrypt_configuration({"version": 1}, "une phrase de sauvegarde solide")
    envelope["kdf"]["n"] = 2**20
    with pytest.raises(BackupError, match="Paramètres"):
        decrypt_configuration(envelope, "une phrase de sauvegarde solide")


def test_backup_derivation_waits_for_the_shared_scrypt_budget() -> None:
    """Each envelope key costs as much memory as a password verification.

    The three backup endpoints answer anyone holding a session, so without the
    process-wide slot a handful of parallel calls walk past the MemoryMax the
    service unit sets and the appliance loses its interface to the OOM killer.
    """
    held = [auth._hashing_slots.acquire(timeout=2) for _ in range(auth.HASHING_SLOTS)]
    assert all(held)
    finished = threading.Event()

    def derive() -> None:
        encrypt_configuration({"version": 1}, PHRASE)
        finished.set()

    worker = threading.Thread(target=derive, daemon=True)
    worker.start()
    try:
        assert not finished.wait(0.5), "la dérivation doit attendre un créneau"
    finally:
        for _ in held:
            auth._hashing_slots.release()

    assert finished.wait(10)
    worker.join(2)


def test_preview_ignores_export_timestamp_and_lists_changed_sections() -> None:
    current = {"version": 1, "exported_at": 20, "tor_policy": {"exit_country": ""}}
    restored = {"version": 1, "exported_at": 10, "tor_policy": {"exit_country": "BE"}}
    assert configuration_diff(current, restored) == [
        {
            "section": "tor_policy",
            "before": {"exit_country": ""},
            "after": {"exit_country": "BE"},
        }
    ]
