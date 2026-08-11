from __future__ import annotations

import pytest
from onionpi.backup import (
    BackupError,
    configuration_diff,
    decrypt_configuration,
    encrypt_configuration,
)


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
