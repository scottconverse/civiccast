#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Prove CivicCast's own copies of the Whistle assets are reachable and exact.

Builds download the Whistle model and the cactus-needle wheel from CivicCast's
third-party release first (see ``civiccast/native/app_payload.py``). This check
downloads each copy and compares it with the size and SHA-256 pinned in that
file, so a missing or altered copy fails a pull request instead of a release.

The pins are read with ``ast`` straight from ``app_payload.py``; nothing from
the ``civiccast`` package is imported, so the check needs only the standard
library.

    python scripts/check_whistle_mirror.py            # download and verify
    python scripts/check_whistle_mirror.py --print    # show what would be checked
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_PAYLOAD = ROOT / "civiccast" / "native" / "app_payload.py"
MIRROR_PREFIX = "https://github.com/scottconverse/civiccast-third-party/releases/download/"

# (mirror URL name, byte-count name, SHA-256 name) in app_payload.py
ASSETS = (
    ("WHISTLE_MODEL_MIRROR_URL", "WHISTLE_MODEL_BYTES", "WHISTLE_MODEL_SHA256"),
    (
        "WHISTLE_ENGINE_WHEEL_MIRROR_URL",
        "WHISTLE_ENGINE_WHEEL_BYTES",
        "WHISTLE_ENGINE_WHEEL_SHA256",
    ),
)


def _evaluate(node: ast.expr, known: dict[str, object]) -> object:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name) and node.id in known:
        return known[node.id]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _evaluate(node.left, known) + _evaluate(node.right, known)  # type: ignore[operator]
    raise ValueError(f"unsupported expression: {ast.dump(node)}")


def read_pins(path: Path = APP_PAYLOAD) -> dict[str, object]:
    """Return the module-level constants of ``path`` that are plain literals."""

    known: dict[str, object] = {}
    for statement in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            target, value = statement.target.id, statement.value
        elif (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
        ):
            target, value = statement.targets[0].id, statement.value
        else:
            continue
        if value is None:
            continue
        try:
            known[target] = _evaluate(value, known)
        except (ValueError, TypeError):
            continue
    return known


def planned_checks(pins: dict[str, object]) -> list[tuple[str, int, str]]:
    checks: list[tuple[str, int, str]] = []
    for url_name, bytes_name, sha_name in ASSETS:
        url, size, sha = pins.get(url_name), pins.get(bytes_name), pins.get(sha_name)
        if not isinstance(url, str) or not url.startswith(MIRROR_PREFIX):
            raise SystemExit(f"{url_name} must be a CivicCast third-party release URL, got {url!r}")
        if not isinstance(size, int) or not isinstance(sha, str) or len(sha) != 64:
            raise SystemExit(f"{bytes_name}/{sha_name} are not a valid size/SHA-256 pin")
        checks.append((url, size, sha))
    return checks


def verify(url: str, size: int, sha: str) -> str | None:
    request = urllib.request.Request(url, headers={"User-Agent": "CivicCast-mirror-check"})  # nosec B310 - fixed HTTPS URL
    digest, total = hashlib.sha256(), 0
    try:
        with urllib.request.urlopen(request, timeout=120) as response:  # nosec B310
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > size:
                    return f"{url}: more than the pinned {size} bytes"
                digest.update(chunk)
    except OSError as exc:
        return f"{url}: {exc}"
    if total != size or digest.hexdigest() != sha:
        return f"{url}: got {total} bytes SHA-256 {digest.hexdigest()}; pinned {size} / {sha}"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--print", action="store_true", help="list the checks without downloading")
    args = parser.parse_args(argv)
    checks = planned_checks(read_pins())
    failures = []
    for url, size, sha in checks:
        if args.print:
            print(f"{url}  {size}  {sha}")
            continue
        problem = verify(url, size, sha)
        print(f"{'FAIL' if problem else 'OK  '} {url}" + (f" -- {problem}" if problem else ""))
        if problem:
            failures.append(problem)
    if failures:
        print(
            f"check_whistle_mirror: FAIL - {len(failures)} of {len(checks)} copies missing or altered"
        )
        return 1
    if not args.print:
        print(f"check_whistle_mirror: PASS - {len(checks)} copies reachable and byte-exact")
    return 0


if __name__ == "__main__":
    sys.exit(main())
