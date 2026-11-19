# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""API tests for S1's /api/staff/station-box-profile[/readiness] and
/api/staff/station/profile (S1 §4/§8, S1 §9 item 4)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from civiccast.app import create_app
from civiccast.installer import station_state as station_state_module
from civiccast.installer.models import FirstAdminSetupRequest
from civiccast.installer.service import complete_first_admin_setup
from civiccast.installer.station_state import (
    resolve_live_captions_enabled,
    resolve_live_captions_enabled_or_default,
)

_OPERATOR_HEADERS = {"Authorization": "Bearer operator-token-a"}


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CIVICCAST_STATION_STATE_PATH", str(tmp_path / "station-state.json"))


def _records_clerk_headers(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.setenv(
        "CIVICCAST_STAFF_TOKENS", "records-token-c:records-c:Records C:records_clerk"
    )
    return {"Authorization": "Bearer records-token-c"}


class TestStationBoxProfileApi:
    def test_get_full_profile_200_for_operator(self) -> None:
        client = TestClient(create_app(), headers=_OPERATOR_HEADERS)
        response = client.get("/api/staff/station-box-profile")
        assert response.status_code == 200
        payload = response.json()
        assert payload["schema_version"] == 1
        assert "hardware" in payload
        assert "engine" in payload
        assert "peg_readiness" in payload
        assert payload["peg_readiness"]["overall"] in ("green", "yellow", "red")

    def test_get_readiness_only_200(self) -> None:
        client = TestClient(create_app(), headers=_OPERATOR_HEADERS)
        response = client.get("/api/staff/station-box-profile/readiness")
        assert response.status_code == 200
        payload = response.json()
        assert set(payload.keys()) == {"overall", "dimensions"}
        assert payload["overall"] in ("green", "yellow", "red")

    def test_403_for_role_without_diagnostic_access(self, monkeypatch: pytest.MonkeyPatch) -> None:
        headers = _records_clerk_headers(monkeypatch)
        client = TestClient(create_app(), headers=headers)
        response = client.get("/api/staff/station-box-profile")
        assert response.status_code == 403

    def test_readiness_403_for_role_without_diagnostic_access(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        headers = _records_clerk_headers(monkeypatch)
        client = TestClient(create_app(), headers=headers)
        response = client.get("/api/staff/station-box-profile/readiness")
        assert response.status_code == 403

    def test_401_with_no_token(self) -> None:
        client = TestClient(create_app())
        response = client.get("/api/staff/station-box-profile")
        assert response.status_code == 401

    def test_extra_field_rejected_by_the_model(self) -> None:
        from pydantic import ValidationError

        from civiccast.platform.station_box_profile import PegReadinessRollup

        with pytest.raises(ValidationError):
            PegReadinessRollup(overall="green", dimensions=[], surprise_field="nope")  # type: ignore[call-arg]


class TestStationIdentityProfileApi:
    def _complete_setup(self) -> None:
        complete_first_admin_setup(
            FirstAdminSetupRequest(
                station_name="Pinegrove School Board",
                admin_display_name="Avery Admin",
                admin_username="avery",
                admin_password="correct horse battery staple",
                recovery_kit_destination="safe",
            )
        )

    def test_get_profile_404_before_setup(self) -> None:
        client = TestClient(create_app(), headers=_OPERATOR_HEADERS)
        response = client.get("/api/staff/station/profile")
        assert response.status_code == 404

    def test_get_profile_200_after_setup(self) -> None:
        self._complete_setup()
        client = TestClient(create_app(), headers=_OPERATOR_HEADERS)
        response = client.get("/api/staff/station/profile")
        assert response.status_code == 200
        payload = response.json()
        assert payload["station_name"] == "Pinegrove School Board"

    def test_publish_operator_reads_only_station_timezone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._complete_setup()
        monkeypatch.setenv(
            "CIVICCAST_STAFF_TOKENS",
            "publish-token-p:publish-p:Publish P:publish_operator",
        )
        monkeypatch.setenv("CIVICCAST_STAFF_TOKENS_FALLBACK_WITH_DB", "1")
        monkeypatch.setenv("CIVICCAST_STATION_TZ", "America/Denver")
        client = TestClient(create_app(), headers={"Authorization": "Bearer publish-token-p"})

        timezone = client.get("/api/staff/station/timezone")

        assert timezone.status_code == 200
        assert timezone.json() == {"station_timezone": "America/Denver"}
        assert client.get("/api/staff/station/profile").status_code == 403
        assert (
            client.put("/api/staff/station/profile", json={"station_name": "Nope"}).status_code
            == 403
        )

    def test_timezone_change_pins_program_slots_before_profile_update(self) -> None:
        from civiccast.installer.service import operator_console_url
        from civiccast.installer.station_state import (
            StationProfileUpdateRequest,
            read_station_setup_state,
        )
        from civiccast.platform.station_router import put_station_profile

        self._complete_setup()
        observed_old_zones: list[str] = []

        class MaterializerStub:
            def pin_legacy_anchors(self) -> None:
                profile = read_station_setup_state(
                    operator_console_url=operator_console_url()
                ).profile
                assert profile is not None
                observed_old_zones.append(profile.station_timezone)

        profile = put_station_profile(
            StationProfileUpdateRequest(station_timezone="America/Denver"),
            program_log_materializer=MaterializerStub(),
        )

        assert observed_old_zones == ["local"]
        assert profile.station_timezone == "America/Denver"

    def test_put_profile_updates_name_and_timezone(self) -> None:
        self._complete_setup()
        client = TestClient(create_app(), headers=_OPERATOR_HEADERS)
        response = client.put(
            "/api/staff/station/profile",
            json={"station_name": "Pinegrove PEG", "station_timezone": "America/Denver"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["station_name"] == "Pinegrove PEG"
        assert payload["station_timezone"] == "America/Denver"

        follow_up = client.get("/api/staff/station/profile")
        assert follow_up.json()["station_name"] == "Pinegrove PEG"

    def test_put_profile_env_override_wins_over_persisted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._complete_setup()
        monkeypatch.setenv("CIVICCAST_STATION_TZ", "America/Chicago")
        client = TestClient(create_app(), headers=_OPERATOR_HEADERS)
        response = client.get("/api/staff/station/profile")
        assert response.status_code == 200
        assert response.json()["station_timezone"] == "America/Chicago"

    def test_put_profile_403_for_non_setup_admin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._complete_setup()
        headers = _records_clerk_headers(monkeypatch)
        client = TestClient(create_app(), headers=headers)
        response = client.put("/api/staff/station/profile", json={"station_name": "Nope"})
        assert response.status_code == 403

    def test_put_profile_404_before_setup(self) -> None:
        client = TestClient(create_app(), headers=_OPERATOR_HEADERS)
        response = client.put("/api/staff/station/profile", json={"station_name": "Too Early"})
        assert response.status_code == 404

    def test_put_profile_rejects_unknown_field(self) -> None:
        self._complete_setup()
        client = TestClient(create_app(), headers=_OPERATOR_HEADERS)
        response = client.put("/api/staff/station/profile", json={"not_a_real_field": "nope"})
        assert response.status_code == 422

    def test_live_captions_are_on_by_default_and_can_be_switched_off(self) -> None:
        """Live captions are ON for a fresh station (owner decision 2026-11-15:
        a station on air without captions is a legal risk).

        First-admin setup persists the value with the operator-choice marker,
        so a later default flip cannot silently change the station, and an
        operator who switches captions off keeps them off across app restarts.
        """

        self._complete_setup()
        client = TestClient(create_app(), headers=_OPERATOR_HEADERS)

        assert client.get("/api/staff/station/profile").json()["live_captions_enabled"] is True
        assert resolve_live_captions_enabled() is True
        # Stored, not merely defaulted: first-admin setup wrote the key.
        from civiccast.installer.models import LIVE_CAPTIONS_CHOICE_KEY
        from civiccast.installer.station_state import _load_raw_state, read_live_captions_enabled

        assert read_live_captions_enabled() is True
        assert _load_raw_state()["station"][LIVE_CAPTIONS_CHOICE_KEY] is True

        response = client.put(
            "/api/staff/station/profile",
            json={"live_captions_enabled": False},
        )
        assert response.status_code == 200
        assert response.json()["live_captions_enabled"] is False

        # Persisted, not just echoed: a fresh app must still see it off.
        follow_up = TestClient(create_app(), headers=_OPERATOR_HEADERS).get(
            "/api/staff/station/profile"
        )
        assert follow_up.json()["live_captions_enabled"] is False
        assert resolve_live_captions_enabled() is False

        # And it can be switched back on.
        back_on = client.put(
            "/api/staff/station/profile",
            json={"live_captions_enabled": True},
        )
        assert back_on.json()["live_captions_enabled"] is True
        assert resolve_live_captions_enabled() is True

    def test_editing_another_field_does_not_disturb_the_caption_switch(self) -> None:
        # OFF is the non-default value now, so it is the one an unrelated edit
        # could plausibly lose by re-applying the default.
        self._complete_setup()
        client = TestClient(create_app(), headers=_OPERATOR_HEADERS)
        client.put("/api/staff/station/profile", json={"live_captions_enabled": False})

        client.put("/api/staff/station/profile", json={"station_name": "Pinegrove PEG"})

        payload = client.get("/api/staff/station/profile").json()
        assert payload["station_name"] == "Pinegrove PEG"
        assert payload["live_captions_enabled"] is False

    def test_caption_tap_off_in_the_environment_wins_over_persisted_on(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Precedence is asymmetric, and only in the safe direction.

        The environment may force live captions OFF; it may never force them
        back ON against an operator who switched them off -- an activated
        native station sets `CIVICCAST_CAPTION_TAP=inline` unconditionally, so
        the reverse precedence would make the switch unreachable on exactly
        the deployments that need it.
        """

        self._complete_setup()
        client = TestClient(create_app(), headers=_OPERATOR_HEADERS)
        # The operator turned them on; the environment still wins in the OFF
        # direction only.
        client.put("/api/staff/station/profile", json={"live_captions_enabled": True})
        monkeypatch.setenv("CIVICCAST_CAPTION_TAP", "off")

        assert client.get("/api/staff/station/profile").json()["live_captions_enabled"] is False
        assert resolve_live_captions_enabled() is False

        monkeypatch.setenv("CIVICCAST_CAPTION_TAP", "inline")
        client.put("/api/staff/station/profile", json={"live_captions_enabled": False})
        assert resolve_live_captions_enabled() is False


class TestLiveCaptionSwitchRehydration:
    """An upgrade from a station commissioned by an older build.

    A beta.4 station-state file has no ``live_captions_enabled`` key at all;
    a beta.5 to rc.6 file stores ``false`` (the old shipped default) without
    the post-rc.6 operator-choice marker. After rc.6 both read as the shipped
    default -- ON (``LIVE_CAPTIONS_DEFAULT``, owner decision 2026-11-15) --
    once; an operator who turns captions off after the upgrade keeps them
    off. Nothing here rewrites a stored value.
    """

    def _beta4_shape_state(self) -> None:
        from civiccast.installer.station_state import _save_raw_state

        _save_raw_state(
            {
                "setup_complete": True,
                "station": {
                    "station_name": "Pinegrove School Board",
                    "admin_display_name": "Avery Admin",
                    "admin_username": "avery",
                    "default_channel_id": "government",
                    "public_base_url": None,
                    "station_timezone": "America/Denver",
                    "storage_locations": {
                        "media_library": "C:/CivicCast/media",
                        "recordings": "C:/CivicCast/recordings",
                        "backups": "C:/CivicCast/backups",
                    },
                    "channel_count": 3,
                    "recovery_kit_id": "rk_beta4",
                    "recovery_kit_generated_at": "2026-06-01T00:00:00Z",
                },
            }
        )

    def test_an_absent_key_rehydrates_as_the_default_on(self) -> None:
        from civiccast.installer.models import LIVE_CAPTIONS_DEFAULT
        from civiccast.installer.station_state import (
            read_live_captions_enabled,
            read_station_setup_state,
        )

        assert LIVE_CAPTIONS_DEFAULT is True, (
            "live captions ship ON by default (owner decision 2026-11-15)"
        )
        self._beta4_shape_state()

        # The raw reader reports "never set", which is NOT the same as False...
        assert read_live_captions_enabled() is None
        # ...the resolver and the rehydrated profile both land on the default.
        assert resolve_live_captions_enabled() is True
        state = read_station_setup_state(operator_console_url="http://localhost:8080")
        assert state.profile is not None
        assert state.profile.live_captions_enabled is True
        # And rehydrating does not write the key: the file still says "never set".
        assert read_live_captions_enabled() is None

    def test_an_older_build_saved_off_is_turned_on_once(self) -> None:
        """The post-rc.6 one-time migration: a ``false`` stored by beta.5 to rc.6
        (no operator-choice marker) reads as on, in the resolver AND in the
        profile the operator console shows."""
        from civiccast.installer.station_state import (
            _load_raw_state,
            _save_raw_state,
            read_live_captions_enabled,
            read_station_setup_state,
        )

        self._beta4_shape_state()
        raw = _load_raw_state()
        raw["station"]["live_captions_enabled"] = False
        _save_raw_state(raw)

        assert read_live_captions_enabled() is None
        assert resolve_live_captions_enabled() is True
        state = read_station_setup_state(operator_console_url="http://localhost:8080")
        assert state.profile is not None
        assert state.profile.live_captions_enabled is True

    def test_off_saved_after_the_upgrade_stays_off(self) -> None:
        """An operator who turns captions off after rc.6 keeps them off:
        the save writes the operator-choice marker."""
        from civiccast.installer.models import LIVE_CAPTIONS_CHOICE_KEY
        from civiccast.installer.station_state import (
            StationProfileUpdateRequest,
            _load_raw_state,
            _save_raw_state,
            read_live_captions_enabled,
            read_station_setup_state,
            update_station_profile_fields,
        )

        self._beta4_shape_state()
        raw = _load_raw_state()
        raw["station"]["live_captions_enabled"] = False
        _save_raw_state(raw)
        assert resolve_live_captions_enabled() is True  # migrated on

        profile = update_station_profile_fields(
            StationProfileUpdateRequest(live_captions_enabled=False)
        )

        assert profile.live_captions_enabled is False
        assert _load_raw_state()["station"][LIVE_CAPTIONS_CHOICE_KEY] is True
        assert read_live_captions_enabled() is False
        assert resolve_live_captions_enabled() is False
        state = read_station_setup_state(operator_console_url="http://localhost:8080")
        assert state.profile is not None
        assert state.profile.live_captions_enabled is False

    def test_env_off_still_wins_over_a_migrated_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from civiccast.installer.station_state import _load_raw_state, _save_raw_state

        self._beta4_shape_state()
        raw = _load_raw_state()
        raw["station"]["live_captions_enabled"] = False
        _save_raw_state(raw)
        monkeypatch.setenv("CIVICCAST_CAPTION_TAP", "off")

        assert resolve_live_captions_enabled() is False

    def test_an_explicitly_stored_on_survives_the_upgrade(self) -> None:
        """The operator's stored choice is kept across a default flip."""
        from civiccast.installer.station_state import (
            _load_raw_state,
            _save_raw_state,
            read_live_captions_enabled,
            read_station_setup_state,
        )

        self._beta4_shape_state()
        raw = _load_raw_state()
        raw["station"]["live_captions_enabled"] = True
        _save_raw_state(raw)

        assert read_live_captions_enabled() is True
        assert resolve_live_captions_enabled() is True
        state = read_station_setup_state(operator_console_url="http://localhost:8080")
        assert state.profile is not None
        assert state.profile.live_captions_enabled is True

    def test_the_switch_survives_being_set_on_an_upgraded_state_file(self) -> None:
        from civiccast.installer.station_state import (
            StationProfileUpdateRequest,
            update_station_profile_fields,
        )

        self._beta4_shape_state()

        profile = update_station_profile_fields(
            StationProfileUpdateRequest(live_captions_enabled=False)
        )

        assert profile.live_captions_enabled is False
        assert resolve_live_captions_enabled() is False


class TestTolerantLiveCaptionResolver:
    """``resolve_live_captions_enabled_or_default`` is what every runtime reader
    of the switch (egress strategy, safe-to-air banner, caption tap/feed/proof
    ``is_enabled``) calls: a locked or corrupt ``station-state.json`` must
    land on the shipped default and be logged once per process, never abort a
    worker's ``run_once`` every 2-30 s with a traceback."""

    def test_a_read_failure_lands_on_the_default_and_logs_once(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        from civiccast.installer.models import LIVE_CAPTIONS_DEFAULT

        station_state_module._live_captions_read_failure_announced = False
        calls: list[int] = []

        def _locked() -> bool:
            calls.append(1)
            raise PermissionError(
                13, "The process cannot access the file because it is being used by another process"
            )

        monkeypatch.setattr(station_state_module, "resolve_live_captions_enabled", _locked)
        needle = "could not read the live-captions station-profile switch"
        with caplog.at_level("WARNING", logger="civiccast.installer.station_state"):
            for _ in range(5):
                assert resolve_live_captions_enabled_or_default() is LIVE_CAPTIONS_DEFAULT
        assert len(calls) == 5  # every call still tries the real read
        assert caplog.text.count(needle) == 1  # ...but the failure is announced once
        assert station_state_module._live_captions_read_failure_announced is True

    def test_a_successful_read_passes_through_unchanged(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        station_state_module._live_captions_read_failure_announced = False
        monkeypatch.setattr(station_state_module, "resolve_live_captions_enabled", lambda: True)
        with caplog.at_level("WARNING", logger="civiccast.installer.station_state"):
            assert resolve_live_captions_enabled_or_default() is True
        assert "could not read the live-captions" not in caplog.text
        assert station_state_module._live_captions_read_failure_announced is False
