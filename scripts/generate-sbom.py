#!/usr/bin/env python3
"""Generate the deterministic SPDX inventory shipped beside each release."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def package(identifier: str, name: str, version: str, kind: str) -> dict[str, Any]:
    return {
        "SPDXID": identifier,
        "name": name,
        "versionInfo": version,
        "downloadLocation": "NOASSERTION",
        "filesAnalyzed": False,
        "licenseConcluded": "NOASSERTION",
        "licenseDeclared": "NOASSERTION",
        "supplier": f"Organization: {kind}",
    }


def identifier(prefix: str, name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9.-]", "-", name).strip("-")
    return f"SPDXRef-{prefix}-{cleaned}"


def source_epoch(root: Path) -> int:
    raw = os.getenv("SOURCE_DATE_EPOCH", "")
    if raw.isdigit():
        return int(raw)
    try:
        return int(subprocess.check_output(["git", "log", "-1", "--format=%ct"], cwd=root))
    except (OSError, ValueError, subprocess.CalledProcessError):
        return 0


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: generate-sbom.py VERSION OUTPUT", file=sys.stderr)
        return 2
    version, output = sys.argv[1], Path(sys.argv[2])
    root = Path(__file__).resolve().parents[1]
    packages = [package("SPDXRef-OnionPi", "onionpi", version, "OnionPi")]

    for line in (root / "backend/requirements.txt").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-r "):
            continue
        match = re.match(r"([A-Za-z0-9_.-]+)(?:\[[^]]+\])?==([^; ]+)", line)
        if match:
            name, dependency_version = match.groups()
            packages.append(
                package(identifier("PyPI", name), name, dependency_version, "PyPI")
            )

    lock = json.loads((root / "frontend/package-lock.json").read_text())
    seen: set[tuple[str, str]] = set()
    for path, metadata in sorted(lock.get("packages", {}).items()):
        if not path.startswith("node_modules/") or not isinstance(metadata, dict):
            continue
        name = path.removeprefix("node_modules/")
        dependency_version = str(metadata.get("version", ""))
        if not dependency_version or (name, dependency_version) in seen:
            continue
        seen.add((name, dependency_version))
        packages.append(
            package(identifier("NPM", f"{name}-{dependency_version}"), name, dependency_version, "npm")
        )

    created = datetime.fromtimestamp(source_epoch(root), tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    document = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"onionpi-{version}",
        "documentNamespace": f"https://github.com/0xmagicduck/onionpi-router/releases/tag/v{version}/sbom",
        "creationInfo": {"created": created, "creators": ["Tool: OnionPi generate-sbom.py"]},
        "packages": packages,
        "relationships": [
            {"spdxElementId": "SPDXRef-DOCUMENT", "relationshipType": "DESCRIBES", "relatedSpdxElement": "SPDXRef-OnionPi"},
            *[
                {"spdxElementId": "SPDXRef-OnionPi", "relationshipType": "DEPENDS_ON", "relatedSpdxElement": item["SPDXID"]}
                for item in packages[1:]
            ],
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
