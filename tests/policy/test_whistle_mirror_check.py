# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Offline checks for scripts/check_whistle_mirror.py (no network)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_whistle_mirror.py"
WORKFLOW = ROOT / ".github" / "workflows" / "ci-third-party-mirror.yml"


def _load():
    spec = importlib.util.spec_from_file_location("check_whistle_mirror", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_check_reads_the_same_pins_the_builder_uses() -> None:
    from civiccast.native import app_payload

    checks = _load().planned_checks(_load().read_pins())
    assert checks == [
        (
            app_payload.WHISTLE_MODEL_MIRROR_URL,
            app_payload.WHISTLE_MODEL_BYTES,
            app_payload.WHISTLE_MODEL_SHA256,
        ),
        (
            app_payload.WHISTLE_ENGINE_WHEEL_MIRROR_URL,
            app_payload.WHISTLE_ENGINE_WHEEL_BYTES,
            app_payload.WHISTLE_ENGINE_WHEEL_SHA256,
        ),
    ]


def test_the_check_refuses_a_mirror_url_outside_civiccast_releases(tmp_path: Path) -> None:
    module = _load()
    pins = module.read_pins()
    pins["WHISTLE_MODEL_MIRROR_URL"] = (
        "https://huggingface.co/Cactus-Compute/whistle/resolve/main/whistle.cact"
    )
    with pytest.raises(SystemExit, match="third-party release"):
        module.planned_checks(pins)


def test_the_workflow_runs_the_check_on_pull_requests_and_weekly() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "pull_request:" in text
    assert "schedule:" in text
    assert "python3 scripts/check_whistle_mirror.py" in text
