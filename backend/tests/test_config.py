from __future__ import annotations

from pathlib import Path

from onionpi.config import _installed_source_ref


def test_source_ref_is_read_from_the_release_manifest(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("ONIONPI_SOURCE_REF", raising=False)
    expected = "1" * 40
    (tmp_path / "SOURCE_REF").write_text(f"{expected}\n", encoding="ascii")

    assert _installed_source_ref(tmp_path) == expected


def test_source_ref_environment_override_is_validated(
    tmp_path: Path, monkeypatch
) -> None:
    fallback = "2" * 40
    (tmp_path / "SOURCE_REF").write_text(f"{fallback}\n", encoding="ascii")
    monkeypatch.setenv("ONIONPI_SOURCE_REF", "not-a-shell-ref; touch /tmp/no")

    assert _installed_source_ref(tmp_path) == fallback


def test_source_ref_environment_override_wins(tmp_path: Path, monkeypatch) -> None:
    expected = "A" * 40
    (tmp_path / "SOURCE_REF").write_text(f"{'2' * 40}\n", encoding="ascii")
    monkeypatch.setenv("ONIONPI_SOURCE_REF", expected)

    assert _installed_source_ref(tmp_path) == expected.lower()
