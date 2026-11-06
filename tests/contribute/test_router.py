# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Contributor workflow router tests."""

from __future__ import annotations

import hashlib
import shutil
import subprocess as sp
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from civiccast.app import create_app
from civiccast.stream._ffmpeg import resolve_h264_encoder

_STAFF_HEADERS = {"Authorization": "Bearer operator-token-a"}

_FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
_FFMPEG_SKIP = pytest.mark.skipif(
    not _FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not on PATH; real-ingest test skipped"
)


def _client(
    monkeypatch: MonkeyPatch, upload_dir: Path, *, asset_upload_dir: Path | None = None
) -> TestClient:
    monkeypatch.setenv("CIVICCAST_ALLOW_EPHEMERAL_STORES", "1")
    monkeypatch.setenv("CIVICCAST_CONTRIBUTOR_UPLOAD_DIR", str(upload_dir))
    # TASK A: accept/schedule ingest into the SAME asset-library upload root
    # the staff /assets/upload and watch-folder paths use (resolved via
    # civiccast.schedule.paths.resolve_upload_root -> CIVICCAST_UPLOAD_DIR).
    # A dedicated directory (never the contributor intake dir) mirrors how a
    # real station configures these as two distinct locations.
    monkeypatch.setenv(
        "CIVICCAST_UPLOAD_DIR", str(asset_upload_dir or (upload_dir / "asset-library"))
    )
    return TestClient(create_app())


def _real_video(
    upload_dir: Path, *, name: str = "show-one.mp4", duration: int = 2
) -> dict[str, object]:
    """Generate a REAL small H.264 mp4 via ffmpeg lavfi and describe it accurately.

    Used by the accept/schedule end-to-end tests so the broken-media probe
    and the asset ingest both run against genuine media, not a placeholder
    byte string -- exercising the real ffprobe pipeline the field evidence
    found was never being run at all.
    """
    upload_dir.mkdir(parents=True, exist_ok=True)
    path = upload_dir / name
    sp.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=duration={duration}:size=320x240:rate=10",
            "-c:v",
            resolve_h264_encoder(),
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        capture_output=True,
        check=True,
    )
    content = path.read_bytes()
    return {
        "upload_ref": str(path),
        "filename": name,
        "content_type": "video/mp4",
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _real_media(
    upload_dir: Path,
    name: str = "show-one.mov",
    content: bytes = b"real-video-bytes",
) -> dict[str, object]:
    """Write a REAL file inside *upload_dir* and describe it accurately.

    QA-3: store.create_submission now resolves ``upload_ref`` against the
    contributor upload directory and recomputes ``sha256``/``size_bytes``
    from the file on disk, so a fabricated path (like the old
    ``uploads/arts-center/show-one.mov`` placeholder) is rejected outright.
    """
    upload_dir.mkdir(parents=True, exist_ok=True)
    path = upload_dir / name
    path.write_bytes(content)
    return {
        "upload_ref": str(path),
        "filename": name,
        "content_type": "video/quicktime",
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _payload(media: dict[str, object]) -> dict[str, object]:
    return {
        "contributor": {
            "account_id": "arts-center",
            "display_name": "Arts Center",
            "contact_email": "producer@example.test",
            "organization": "Arts Center",
        },
        "channel_id": "public",
        "title": "Community Arts Magazine",
        "description": "A half-hour program from local arts producers.",
        "tags": ["arts", "community"],
        "producer_name": "Producer One",
        "requested_air_date": "2026-05-31T18:00:00Z",
        "media": media,
        "agreements": [
            {
                "agreement_id": "community-media-submission",
                "version": "2026-05-31",
                "accepted_at": "2026-05-31T18:00:00Z",
                "accepted_by_name": "Producer One",
            }
        ],
        "notifications": [{"kind": "email", "target": "producer@example.test"}],
    }


def test_public_submission_flow_separates_contributor_from_operator_queue(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = _client(monkeypatch, tmp_path)

    agreement = client.get("/api/public/contribute/agreements/current")
    created = client.post(
        "/api/public/contribute/submissions", json=_payload(_real_media(tmp_path))
    )

    assert agreement.status_code == 200
    assert agreement.json()["agreement_id"] == "community-media-submission"
    assert created.status_code == 201, created.text
    receipt = created.json()
    status = client.get(
        f"/api/public/contribute/submissions/{receipt['submission_id']}/status",
        params={"receipt_token": receipt["receipt_token"]},
    )
    queue = client.get("/api/staff/contribute/submissions", headers=_STAFF_HEADERS)

    assert status.status_code == 200
    assert status.json()["state"] == "submitted"
    assert queue.status_code == 200
    assert queue.json()["needs_operator_action"] == 1
    assert queue.json()["submissions"][0]["contributor"]["tier"] == "contributor"


def test_public_upload_endpoint_returns_media_reference(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/api/public/contribute/uploads",
        files={"file": ("program.mov", b"video-bytes", "video/quicktime")},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["filename"] == "program.mov"
    assert payload["content_type"] == "video/quicktime"
    assert payload["size_bytes"] == len(b"video-bytes")
    assert len(payload["sha256"]) == 64
    # Field evidence / GauntletGate finding: upload_ref must be an opaque
    # handle, never the absolute server path (which used to reveal the
    # service account's profile layout) and never even the contributor's
    # own filename (guessable -- see store.resolve_contributor_upload_path's
    # docstring for why that mattered beyond privacy).
    upload_ref = payload["upload_ref"]
    assert upload_ref != "program.mov"
    assert upload_ref != str(tmp_path / "program.mov")
    assert "/" not in upload_ref
    assert "\\" not in upload_ref
    assert str(tmp_path) not in upload_ref
    assert upload_ref.endswith(".mov")
    assert (tmp_path / upload_ref).exists()
    assert not (tmp_path / "program.mov").exists()


def test_public_upload_then_submit_round_trip_succeeds_with_the_opaque_ref(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Task D / field evidence follow-up: the real two-step flow, end to end.

    Every other test in this module builds its ``SubmissionMediaReference``
    by hand (writing a file directly and passing ``str(path)``) rather than
    going through the real ``POST /uploads`` response -- which would have
    hidden a mismatch between what the upload endpoint now returns (an
    opaque token) and what the submission endpoint expects to resolve. This
    drives the actual two calls a real contributor portal makes, verbatim.
    """
    client = _client(monkeypatch, tmp_path)

    upload_response = client.post(
        "/api/public/contribute/uploads",
        files={"file": ("real-program.mov", b"a-real-programs-worth-of-bytes", "video/quicktime")},
    )
    assert upload_response.status_code == 201, upload_response.text
    media = upload_response.json()

    submit_response = client.post("/api/public/contribute/submissions", json=_payload(media))
    assert submit_response.status_code == 201, submit_response.text
    receipt = submit_response.json()
    assert receipt["state"] == "submitted"

    status_response = client.get(
        f"/api/public/contribute/submissions/{receipt['submission_id']}/status",
        params={"receipt_token": receipt["receipt_token"]},
    )
    assert status_response.status_code == 200
    assert status_response.json()["state"] == "submitted"

    # The staff view proves the submission's media now points at the SAME
    # real file the upload endpoint wrote, resolved through the opaque ref.
    staff_view = client.get(
        f"/api/staff/contribute/submissions/{receipt['submission_id']}",
        headers=_STAFF_HEADERS,
    )
    assert staff_view.status_code == 200
    assert staff_view.json()["media"]["upload_ref"] == media["upload_ref"]
    assert staff_view.json()["media"]["sha256"] == media["sha256"]


def test_public_status_requires_receipt_token(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    created = client.post(
        "/api/public/contribute/submissions", json=_payload(_real_media(tmp_path))
    ).json()

    response = client.get(
        f"/api/public/contribute/submissions/{created['submission_id']}/status",
        params={"receipt_token": "x" * 24},
    )

    assert response.status_code == 403


@_FFMPEG_SKIP
def test_staff_review_can_edit_accept_schedule_and_report(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """TASK A/D end-to-end: accept ingests a REAL library asset (retrievable
    via the staff asset library) and send-to-schedule creates a REAL,
    non-null schedule item (retrievable on the schedule) -- reusing the
    exact staff-upload/watch-folder ffprobe ingest and schedule-item-create
    paths, never a parallel pipeline.
    """
    client = _client(monkeypatch, tmp_path)
    created = client.post(
        "/api/public/contribute/submissions", json=_payload(_real_video(tmp_path))
    ).json()

    accepted = client.post(
        f"/api/staff/contribute/submissions/{created['submission_id']}/review",
        headers=_STAFF_HEADERS,
        json={
            "action": "accept",
            "metadata_patch": {"title": "Edited Community Arts Magazine"},
        },
    )
    assert accepted.status_code == 200, accepted.text
    accepted_body = accepted.json()
    assert accepted_body["title"] == "Edited Community Arts Magazine"
    assert accepted_body["state"] == "accepted"
    # TASK C: the gate is now a REAL automated ffprobe verdict, not a
    # client-supplied attestation -- summary names real probe evidence.
    assert accepted_body["broken_media_gate"]["state"] == "passed"
    assert "ffprobe" in accepted_body["broken_media_gate"]["summary"].lower()

    # TASK A: acceptance must produce a REAL, retrievable library asset.
    asset_id = accepted_body["asset_id"]
    assert asset_id, "accepted submission must carry a real library asset_id"
    asset = client.get(f"/api/staff/assets/{asset_id}", headers=_STAFF_HEADERS)
    assert asset.status_code == 200, asset.text
    assert asset.json()["codec_video"] == "h264"
    assert asset.json()["duration_seconds"] == 2
    assert asset.json()["state"] == "validated"

    scheduled = client.post(
        f"/api/staff/contribute/submissions/{created['submission_id']}/review",
        headers=_STAFF_HEADERS,
        json={
            "action": "schedule",
            "schedule_handoff": {
                "channel_id": "public",
                "requested_start": "2026-06-01T18:00:00Z",
                "duration_seconds": 1800,
            },
        },
    )
    assert scheduled.status_code == 200, scheduled.text
    scheduled_body = scheduled.json()
    assert scheduled_body["state"] == "scheduled"
    assert scheduled_body["schedule_handoff"]["channel_id"] == "public"

    # TASK A: schedule_item_id must never be null on a success response, and
    # the item must actually exist on the schedule.
    schedule_item_id = scheduled_body["schedule_handoff"]["schedule_item_id"]
    assert schedule_item_id, "scheduled submission must carry a real schedule_item_id"
    schedule_item = client.get(f"/api/staff/schedule/{schedule_item_id}", headers=_STAFF_HEADERS)
    assert schedule_item.status_code == 200, schedule_item.text
    assert schedule_item.json()["asset_id"] == asset_id
    assert schedule_item.json()["channel_id"] == "public"
    schedule_list = client.get("/api/staff/schedule", headers=_STAFF_HEADERS)
    assert schedule_item_id in {item["id"] for item in schedule_list.json()}

    # TASK B: the resident-facing status message is only shown once it is
    # actually true.
    public_status = client.get(
        f"/api/public/contribute/submissions/{created['submission_id']}/status",
        params={"receipt_token": created["receipt_token"]},
    )
    assert public_status.status_code == 200
    assert public_status.json()["schedule_handoff"]["schedule_item_id"] == schedule_item_id
    assert "real spot on the schedule" in public_status.json()["status_message"]

    report = client.get("/api/staff/contribute/reports/producers", headers=_STAFF_HEADERS)
    outbox = client.get("/api/staff/contribute/notifications/outbox", headers=_STAFF_HEADERS)

    assert report.status_code == 200
    assert report.json()["rows"][0]["scheduled_count"] == 1
    assert outbox.status_code == 200
    assert [notice["state"] for notice in outbox.json()["notifications"]] == [
        "submitted",
        "accepted",
        "scheduled",
    ]


@_FFMPEG_SKIP
def test_accepted_submission_asset_reaches_packageable_state(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """night-a4 field gap: accept must produce media an operator can actually air.

    PR #65 (fix/contributor-accept-schedule-ingest) proved accept ingests a
    real, retrievable library asset and made ``schedule_item_id`` non-null --
    but its own end-to-end test never called the packaging step, so it never
    caught that ``civiccast.app._EphemeralAssetStore`` (the store
    ``get_postgres_store`` resolves to in throwaway/dev mode, exactly the
    mode a quick board-demo box runs in without ``DATABASE_URL``) had no
    ``mark_packaged``/``mark_unpublished`` methods. Packaging ANY validated
    asset in that mode -- including one a contributor's accept had just
    ingested -- raised ``AttributeError`` and came back as a 503 that never
    recorded a ``manifest_url``. The submission genuinely reached "accepted"
    with a real ``asset_id`` (this part of #65 worked), but that asset could
    never become truly airable: no manifest, nothing to package, nothing a
    resident could ever watch -- exactly what the field survey called "never
    turns into an airable asset" even after #65 merged. This drives the real
    operator sequence past where #65's own test stopped: accept, package,
    and confirm the asset carries a real playback manifest before/while it
    also has a real schedule item -- and that unpublish (idempotent, per
    the Postgres sibling's contract) doesn't blow up either.
    """
    client = _client(monkeypatch, tmp_path)
    created = client.post(
        "/api/public/contribute/submissions", json=_payload(_real_video(tmp_path))
    ).json()

    accepted = client.post(
        f"/api/staff/contribute/submissions/{created['submission_id']}/review",
        headers=_STAFF_HEADERS,
        json={"action": "accept"},
    )
    assert accepted.status_code == 200, accepted.text
    asset_id = accepted.json()["asset_id"]
    assert asset_id

    before_package = client.get(f"/api/staff/assets/{asset_id}", headers=_STAFF_HEADERS)
    assert before_package.status_code == 200
    assert before_package.json()["manifest_url"] is None

    packaged = client.post(f"/api/staff/assets/{asset_id}/package", headers=_STAFF_HEADERS)
    assert packaged.status_code == 200, packaged.text
    assert packaged.json()["manifest_url"], "packaging must record a real playback manifest"
    assert packaged.json()["manifest_url"].endswith("/playlist.m3u8")

    after_package = client.get(f"/api/staff/assets/{asset_id}", headers=_STAFF_HEADERS)
    assert after_package.status_code == 200
    assert after_package.json()["manifest_url"] == packaged.json()["manifest_url"]

    # The submission can still be sent to the schedule after packaging --
    # the two are independent operator actions, and neither one silently
    # breaks the other.
    scheduled = client.post(
        f"/api/staff/contribute/submissions/{created['submission_id']}/review",
        headers=_STAFF_HEADERS,
        json={
            "action": "schedule",
            "schedule_handoff": {
                "channel_id": "public",
                "requested_start": "2026-06-01T18:00:00Z",
                "duration_seconds": 1800,
            },
        },
    )
    assert scheduled.status_code == 200, scheduled.text
    assert scheduled.json()["schedule_handoff"]["schedule_item_id"]

    # Idempotent unpublish must not 500 on a never-published, freshly
    # packaged asset -- same contract as PostgresAssetStore.mark_unpublished.
    unpublish = client.post(f"/api/staff/assets/{asset_id}/unpublish", headers=_STAFF_HEADERS)
    assert unpublish.status_code == 200, unpublish.text
    assert unpublish.json()["published_at"] is None


@_FFMPEG_SKIP
def test_send_to_schedule_before_accept_is_refused_not_silently_broken(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """TASK A/B: scheduling a submission that was never accepted (no library
    asset exists yet) must fail loudly and say what operator action remains
    -- not report a fake success with a null schedule_item_id."""
    client = _client(monkeypatch, tmp_path)
    created = client.post(
        "/api/public/contribute/submissions", json=_payload(_real_video(tmp_path))
    ).json()

    response = client.post(
        f"/api/staff/contribute/submissions/{created['submission_id']}/review",
        headers=_STAFF_HEADERS,
        json={
            "action": "schedule",
            "schedule_handoff": {
                "channel_id": "public",
                "requested_start": "2026-06-01T18:00:00Z",
                "duration_seconds": 1800,
            },
        },
    )

    assert response.status_code == 409
    assert "Accept it" in response.json()["detail"]


@_FFMPEG_SKIP
def test_accept_rejects_a_corrupt_file_via_the_real_probe(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """TASK C/D: a deliberately corrupt/unreadable file must be caught by the
    REAL ffprobe probe -- accept fails with 422 and the submission is left
    unaccepted, never silently flipped to "accepted" on a media-less claim."""
    client = _client(monkeypatch, tmp_path)
    upload_dir = tmp_path
    upload_dir.mkdir(parents=True, exist_ok=True)
    corrupt_path = upload_dir / "corrupt-show.mp4"
    corrupt_path.write_bytes(b"this is not a real video file, just garbage bytes" * 20)
    corrupt_media = {
        "upload_ref": str(corrupt_path),
        "filename": "corrupt-show.mp4",
        "content_type": "video/mp4",
        "size_bytes": corrupt_path.stat().st_size,
        "sha256": hashlib.sha256(corrupt_path.read_bytes()).hexdigest(),
    }
    created = client.post("/api/public/contribute/submissions", json=_payload(corrupt_media)).json()

    response = client.post(
        f"/api/staff/contribute/submissions/{created['submission_id']}/review",
        headers=_STAFF_HEADERS,
        json={"action": "accept"},
    )

    assert response.status_code == 422, response.text
    queue = client.get("/api/staff/contribute/submissions", headers=_STAFF_HEADERS)
    row = next(
        item
        for item in queue.json()["submissions"]
        if item["submission_id"] == created["submission_id"]
    )
    assert row["state"] == "submitted"
    assert row["asset_id"] is None


@_FFMPEG_SKIP
def test_accept_operator_override_ingests_without_a_fabricated_pass(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """TASK C: an explicit operator override still ingests the file (so it
    can be scheduled) but the gate persists the operator's own
    ``override_accepted`` attestation -- never silently rewritten to a
    fabricated automated "passed" verdict."""
    client = _client(monkeypatch, tmp_path)
    created = client.post(
        "/api/public/contribute/submissions", json=_payload(_real_video(tmp_path))
    ).json()

    response = client.post(
        f"/api/staff/contribute/submissions/{created['submission_id']}/review",
        headers=_STAFF_HEADERS,
        json={
            "action": "accept",
            "broken_media_gate": {
                "state": "override_accepted",
                "checked_at": "2026-05-31T18:05:00Z",
                "summary": "Operator manually reviewed and approved despite the probe.",
            },
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["asset_id"], "override must still ingest a real library asset"
    assert body["broken_media_gate"]["state"] == "override_accepted"
    assert body["broken_media_gate"]["summary"] == (
        "Operator manually reviewed and approved despite the probe."
    )


def test_accept_operator_override_cannot_ingest_an_xml_document(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """CVE-2026-120210: an IMF composition playlist (XML) submitted as a
    "video" must never become station media, even when an operator overrides
    a failed probe -- ffmpeg's IMF demuxer would follow its ASSETMAP paths
    outside the package. The refusal happens before ffprobe runs, so it is
    not the overridable probe failure."""
    real_run = sp.run
    ffprobe_calls: list[object] = []

    def fake_run(cmd: object, *args: object, **kwargs: object) -> object:
        if isinstance(cmd, list) and cmd and cmd[0] == "ffprobe":
            # Stand-in for ffprobe failing on the document (no ffmpeg needed).
            ffprobe_calls.append(cmd)
            return sp.CompletedProcess(cmd, 1, stdout="", stderr="Invalid data")
        return real_run(cmd, *args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr("civiccast.schedule.ingest.shutil.which", lambda _name: "ffprobe")
    monkeypatch.setattr("civiccast.schedule.ingest.subprocess.run", fake_run)
    client = _client(monkeypatch, tmp_path)
    cpl = (
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b'<CompositionPlaylist xmlns="http://www.smpte-ra.org/schemas/2067-3/2016">'
        b"<ContentTitle>Council</ContentTitle></CompositionPlaylist>\n"
    )
    created = client.post(
        "/api/public/contribute/submissions",
        json=_payload(_real_media(tmp_path, name="council-meeting.mov", content=cpl)),
    ).json()

    response = client.post(
        f"/api/staff/contribute/submissions/{created['submission_id']}/review",
        headers=_STAFF_HEADERS,
        json={
            "action": "accept",
            "broken_media_gate": {
                "state": "override_accepted",
                "checked_at": "2026-05-31T18:05:00Z",
                "summary": "Operator approved despite the probe.",
            },
        },
    )

    assert response.status_code == 422, response.text
    assert "XML document" in response.json()["detail"]
    assert ffprobe_calls == []
    queue = client.get("/api/staff/contribute/submissions", headers=_STAFF_HEADERS)
    row = next(
        item
        for item in queue.json()["submissions"]
        if item["submission_id"] == created["submission_id"]
    )
    assert row["state"] == "submitted"
    assert row["asset_id"] is None


def test_contributor_openapi_exports_contracts(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)

    schema = client.get("/openapi.json").json()

    assert "/api/public/contribute/submissions" in schema["paths"]
    assert "/api/staff/contribute/submissions" in schema["paths"]
    assert "/api/staff/contribute/reports/producers" in schema["paths"]
    assert "/api/staff/contribute/notifications/outbox" in schema["paths"]
    assert "ContributorSubmissionCreate" in schema["components"]["schemas"]
    assert "ContributorReviewQueue" in schema["components"]["schemas"]
    assert "ContributorNotificationOutbox" in schema["components"]["schemas"]
    assert "ProducerActivityReport" in schema["components"]["schemas"]


def test_public_status_rate_limits_receipt_token_guessing(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """Audit item #27 review: the status route verifies a receipt secret and
    had no rate limit anywhere on the path — the same gap the paywall
    verify/access routes already close. Wrong-token hammering must trip
    429 with Retry-After; the budget is per app instance so other tests
    are unaffected."""

    client = _client(monkeypatch, tmp_path)
    created = client.post(
        "/api/public/contribute/submissions", json=_payload(_real_media(tmp_path))
    ).json()

    url = f"/api/public/contribute/submissions/{created['submission_id']}/status"
    statuses = [
        client.get(url, params={"receipt_token": f"guess-{index:018d}xxxxxx"}).status_code
        for index in range(35)
    ]

    assert statuses[:30] == [403] * 30
    assert set(statuses[30:]) == {429}
    limited = client.get(url, params={"receipt_token": "x" * 24})
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) > 0


def test_create_submission_rejects_a_fabricated_upload_ref(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """QA-3: a client-supplied media reference that isn't a real upload
    inside the station's upload directory must be rejected with 422, not
    silently accepted into the operator review queue."""
    client = _client(monkeypatch, tmp_path)

    fabricated_media = {
        "upload_ref": "uploads/arts-center/show-one.mov",
        "filename": "show-one.mov",
        "content_type": "video/quicktime",
        "size_bytes": 1024,
        "sha256": "A" * 64,
    }

    response = client.post("/api/public/contribute/submissions", json=_payload(fabricated_media))

    assert response.status_code == 422, response.text


class _SlowReadDict(dict[str, int]):
    """A dict that sleeps on every read, to widen the read->write window.

    An earlier version of this test drove the real ``try_reserve`` at 16 threads
    and asserted no overshoot -- but an adversarial review showed the assertion
    held even with the lock REMOVED, because the unlocked critical section is
    only a few bytecode ops and CPython's GIL almost never interleaves it. The
    test proved nothing about the real method. This dict forces the interleave:
    the sleep sits between ``try_reserve``'s read and its write. WITH the lock
    the sleep happens while the lock is held, so threads serialise and no
    overshoot is possible; WITHOUT the lock the sleeps overlap, every thread
    reads the same stale total, and the ceiling is blown -- so this test now
    genuinely fails if the lock is ever removed (verified by hand: reverting
    ``try_reserve`` to a lockless snapshot-then-add makes it red).
    """

    def get(self, key: str, default: int = 0) -> int:  # type: ignore[override]
        # Read the underlying value NOW, then stall, so the caller acts on a
        # snapshot that is stale by the time it writes. The sleep must be AFTER
        # the read, not before: a sleep before the read just delays it until the
        # value is already fresh, which is why an earlier attempt did not race.
        value = super().get(key, default)
        time.sleep(0.01)
        return value


def test_upload_byte_budget_is_atomic_under_concurrency() -> None:
    """QA-2 race: concurrent reservations from one key cannot exceed the ceiling.

    Drives many threads at the REAL ``ContributorUploadByteBudget.try_reserve``,
    with its backing dict instrumented (:class:`_SlowReadDict`) to widen the
    check-then-act window so the race is actually reachable under the GIL. With
    the lock in place the reservations serialise and the committed total never
    exceeds the ceiling; without it they overshoot (see the class docstring).
    """
    from concurrent.futures import ThreadPoolExecutor

    from civiccast.contribute.router import ContributorUploadByteBudget

    ceiling = 10 * 1024
    chunk = 3 * 1024  # three fit (9 KiB); a fourth would exceed 10 KiB
    workers = 16

    budget = ContributorUploadByteBudget()
    budget._totals = _SlowReadDict()  # widen the read->write window
    barrier = threading.Barrier(workers)

    def reserve_once(_: int) -> bool:
        barrier.wait()  # release all threads into try_reserve together
        return budget.try_reserve("1.2.3.4", chunk, ceiling)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(reserve_once, range(workers)))

    committed = sum(1 for ok in results if ok) * chunk
    assert committed <= ceiling, (
        f"concurrent reservations reached {committed} bytes past a {ceiling}-byte "
        "ceiling -- the per-IP byte budget's check-and-increment is not atomic"
    )
    assert budget.total("1.2.3.4") == committed


def test_upload_per_ip_cumulative_budget_blocks_before_the_directory_ceiling(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """QA-2 part 3: a single address must not be able to consume the whole
    contributor-upload directory budget by itself. This must trip well
    BEFORE the (much larger) aggregate directory ceiling, from ONE address
    alone, even though the directory as a whole is nowhere near full."""
    monkeypatch.setenv("CIVICCAST_CONTRIBUTOR_UPLOAD_PER_IP_MAX_BYTES", str(10 * 1024))
    monkeypatch.setenv("CIVICCAST_CONTRIBUTOR_UPLOAD_DIR_MAX_BYTES", str(10 * 1024 * 1024))
    # rc18 re-gate PE-2/TE-1: the directory ceiling now reserves each upload's
    # worst case (the per-request max) up front, so the per-request max must be
    # <= the directory ceiling or every upload is refused before the per-IP
    # budget this test targets can trip. The default max (2 GiB) exceeds the
    # 10 MiB ceiling set here; pin it consistent with the ceiling.
    monkeypatch.setenv("CIVICCAST_CONTRIBUTOR_MAX_UPLOAD_BYTES", str(1024 * 1024))
    monkeypatch.setenv("CIVICCAST_CONTRIBUTOR_UPLOAD_RATE_LIMIT", "100")
    client = _client(monkeypatch, tmp_path)

    chunk = b"x" * (4 * 1024)
    statuses = [
        client.post(
            "/api/public/contribute/uploads",
            files={"file": (f"clip-{i}.mov", chunk, "video/quicktime")},
        ).status_code
        for i in range(4)
    ]

    assert statuses[0] == 201, statuses
    assert statuses[1] == 201, statuses
    assert statuses[2] in {429, 507}, (
        f"a single address uploaded {3 * len(chunk)} bytes against a "
        f"{10 * 1024}-byte per-IP ceiling and was still accepted; statuses={statuses}"
    )
    dir_total = sum(f.stat().st_size for f in tmp_path.iterdir() if f.is_file())
    assert dir_total < 10 * 1024 * 1024, (
        "the per-IP budget must trip well before the aggregate directory "
        f"ceiling -- directory only holds {dir_total} bytes of a 10 MiB ceiling"
    )


# ---------------------------------------------------------------------------
# BLOCKER / CRITICAL / MAJOR: decline must pull a live schedule item, and
# accept/schedule/mark_under_review/request_changes must be guarded against
# racing or reverting a real, already-live booking.
# ---------------------------------------------------------------------------


@_FFMPEG_SKIP
def test_decline_after_schedule_cancels_the_real_schedule_item(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """BLOCKER: declining an already-accepted-and-scheduled submission must
    pull the REAL schedule item off the schedule, not just flip the JSON
    submission state to "declined" -- otherwise the declined recording still
    airs at its scheduled time. Must fail on the pre-fix code: the old
    ``_apply_review`` "decline" branch only ever set
    ``values["state"] = "declined"`` and never touched the schedule store,
    so the schedule item this test checks stayed "scheduled" forever.
    """
    client = _client(monkeypatch, tmp_path)
    created = client.post(
        "/api/public/contribute/submissions", json=_payload(_real_video(tmp_path))
    ).json()

    accepted = client.post(
        f"/api/staff/contribute/submissions/{created['submission_id']}/review",
        headers=_STAFF_HEADERS,
        json={"action": "accept"},
    )
    assert accepted.status_code == 200, accepted.text

    scheduled = client.post(
        f"/api/staff/contribute/submissions/{created['submission_id']}/review",
        headers=_STAFF_HEADERS,
        json={
            "action": "schedule",
            "schedule_handoff": {
                "channel_id": "public",
                "requested_start": "2026-06-01T18:00:00Z",
                "duration_seconds": 1800,
            },
        },
    )
    assert scheduled.status_code == 200, scheduled.text
    schedule_item_id = scheduled.json()["schedule_handoff"]["schedule_item_id"]
    assert schedule_item_id

    before_decline = client.get(f"/api/staff/schedule/{schedule_item_id}", headers=_STAFF_HEADERS)
    assert before_decline.status_code == 200
    assert before_decline.json()["state"] == "scheduled"

    declined = client.post(
        f"/api/staff/contribute/submissions/{created['submission_id']}/review",
        headers=_STAFF_HEADERS,
        json={
            "action": "decline",
            "decline_reason": "A rights dispute surfaced after this was scheduled.",
        },
    )
    assert declined.status_code == 200, declined.text
    assert declined.json()["state"] == "declined"

    after_decline = client.get(f"/api/staff/schedule/{schedule_item_id}", headers=_STAFF_HEADERS)
    assert after_decline.status_code == 200, after_decline.text
    assert after_decline.json()["state"] == "cancelled", (
        "declining a scheduled submission must cancel its real schedule item -- "
        f"got state={after_decline.json()['state']!r}, so the declined recording "
        "would still air"
    )

    # The schedule listing (what an actual playout dispatch would read) must
    # not carry the declined item as an active booking either.
    schedule_list = client.get("/api/staff/schedule", headers=_STAFF_HEADERS)
    assert schedule_list.status_code == 200
    live_states = {item["state"] for item in schedule_list.json() if item["id"] == schedule_item_id}
    assert live_states == {"cancelled"}

    public_status = client.get(
        f"/api/public/contribute/submissions/{created['submission_id']}/status",
        params={"receipt_token": created["receipt_token"]},
    )
    assert public_status.status_code == 200
    assert public_status.json()["state"] == "declined"


@_FFMPEG_SKIP
def test_decline_without_a_schedule_item_still_works(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """Sanity companion to the cancel-on-decline fix: a submission that was
    never scheduled (the common case) must still decline cleanly -- the new
    cancel step is a no-op when there is no real schedule item to pull."""
    client = _client(monkeypatch, tmp_path)
    created = client.post(
        "/api/public/contribute/submissions", json=_payload(_real_media(tmp_path))
    ).json()

    declined = client.post(
        f"/api/staff/contribute/submissions/{created['submission_id']}/review",
        headers=_STAFF_HEADERS,
        json={"action": "decline", "decline_reason": "Not a fit for this channel."},
    )

    assert declined.status_code == 200, declined.text
    assert declined.json()["state"] == "declined"


@_FFMPEG_SKIP
def test_concurrent_duplicate_accept_produces_exactly_one_library_asset(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """CRITICAL race: router.py read the submission's state OUTSIDE the
    store's lock, so two concurrent accepts on the same "submitted"
    submission could both pass the precondition check, both run the real
    ffprobe ingest, and both call ``AssetStore.ingest_upload`` -- two real
    library assets, one permanently orphaned. Drives many real concurrent
    HTTP requests at the same submission (same pattern as
    ``test_upload_byte_budget_is_atomic_under_concurrency`` above: a
    ``threading.Barrier`` releases every worker into the request together).
    With ``ContributorSubmissionStore.reserve_review_action`` in place,
    exactly one request reaches the ingest and gets 200; every other
    concurrent/duplicate request is rejected with 409 before it can touch
    the asset store at all -- so exactly one library asset ever exists.
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor

    client = _client(monkeypatch, tmp_path)
    created = client.post(
        "/api/public/contribute/submissions", json=_payload(_real_video(tmp_path))
    ).json()

    workers = 8
    barrier = threading.Barrier(workers)

    def accept_once(_: int) -> int:
        barrier.wait()  # release every worker into the review POST together
        response = client.post(
            f"/api/staff/contribute/submissions/{created['submission_id']}/review",
            headers=_STAFF_HEADERS,
            json={"action": "accept"},
        )
        return response.status_code

    with ThreadPoolExecutor(max_workers=workers) as pool:
        statuses = list(pool.map(accept_once, range(workers)))

    assert statuses.count(200) == 1, (
        f"exactly one concurrent accept must succeed; statuses={statuses}"
    )
    assert all(code in {200, 409} for code in statuses), statuses

    queue = client.get("/api/staff/contribute/submissions", headers=_STAFF_HEADERS)
    row = next(
        item
        for item in queue.json()["submissions"]
        if item["submission_id"] == created["submission_id"]
    )
    assert row["state"] == "accepted"
    assert row["asset_id"]

    library = client.get("/api/staff/assets", headers=_STAFF_HEADERS)
    assert library.status_code == 200
    contributor_assets = [
        item for item in library.json() if item["asset_id"].startswith("contributor-")
    ]
    assert len(contributor_assets) == 1, (
        "a concurrent double-accept must never leave more than one real library "
        f"asset behind; found {[a['asset_id'] for a in contributor_assets]}"
    )
    assert contributor_assets[0]["asset_id"] == row["asset_id"]


@_FFMPEG_SKIP
def test_concurrent_duplicate_schedule_produces_exactly_one_schedule_row(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """CRITICAL race: the same TOCTOU as the double-accept test above, but for
    send-to-schedule -- two concurrent "schedule" requests on the same
    accepted submission could both pass the precondition check and both call
    ``PostgresScheduleStore.create``/``_EphemeralScheduleStore.create``,
    creating two real, live ``schedule_items`` rows on the SAME channel at
    the SAME time (one an orphaned live booking a resident could actually
    see air). With the reservation in place, exactly one request creates a
    real schedule row and every other concurrent/duplicate request is
    rejected with 409 before it can touch the schedule store.
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor

    client = _client(monkeypatch, tmp_path)
    created = client.post(
        "/api/public/contribute/submissions", json=_payload(_real_video(tmp_path))
    ).json()
    accepted = client.post(
        f"/api/staff/contribute/submissions/{created['submission_id']}/review",
        headers=_STAFF_HEADERS,
        json={"action": "accept"},
    )
    assert accepted.status_code == 200, accepted.text
    asset_id = accepted.json()["asset_id"]

    workers = 8
    barrier = threading.Barrier(workers)
    handoff = {
        "action": "schedule",
        "schedule_handoff": {
            "channel_id": "public",
            "requested_start": "2026-06-01T18:00:00Z",
            "duration_seconds": 1800,
        },
    }

    def schedule_once(_: int) -> int:
        barrier.wait()  # release every worker into the review POST together
        response = client.post(
            f"/api/staff/contribute/submissions/{created['submission_id']}/review",
            headers=_STAFF_HEADERS,
            json=handoff,
        )
        return response.status_code

    with ThreadPoolExecutor(max_workers=workers) as pool:
        statuses = list(pool.map(schedule_once, range(workers)))

    assert statuses.count(200) == 1, (
        f"exactly one concurrent schedule request must succeed; statuses={statuses}"
    )
    assert all(code in {200, 409} for code in statuses), statuses

    schedule_list = client.get("/api/staff/schedule", headers=_STAFF_HEADERS)
    assert schedule_list.status_code == 200
    rows_for_asset = [item for item in schedule_list.json() if item["asset_id"] == asset_id]
    assert len(rows_for_asset) == 1, (
        "a concurrent double-schedule must never leave more than one real, live "
        f"schedule row behind for the same asset; found {rows_for_asset}"
    )


@_FFMPEG_SKIP
def test_mark_under_review_cannot_revert_an_already_scheduled_submission(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """MAJOR: mark_under_review/request_changes had no state guard at all --
    an operator (or a stale/duplicate UI click) could revert an already-
    scheduled submission straight back to "under_review" while the real
    schedule item it produced stayed live and airable, leaving the
    contributor portal and the operator queue disagreeing about whether the
    program is actually going to air."""
    client = _client(monkeypatch, tmp_path)
    created = client.post(
        "/api/public/contribute/submissions", json=_payload(_real_video(tmp_path))
    ).json()
    client.post(
        f"/api/staff/contribute/submissions/{created['submission_id']}/review",
        headers=_STAFF_HEADERS,
        json={"action": "accept"},
    )
    scheduled = client.post(
        f"/api/staff/contribute/submissions/{created['submission_id']}/review",
        headers=_STAFF_HEADERS,
        json={
            "action": "schedule",
            "schedule_handoff": {
                "channel_id": "public",
                "requested_start": "2026-06-01T18:00:00Z",
                "duration_seconds": 1800,
            },
        },
    )
    assert scheduled.status_code == 200, scheduled.text
    schedule_item_id = scheduled.json()["schedule_handoff"]["schedule_item_id"]

    reverted = client.post(
        f"/api/staff/contribute/submissions/{created['submission_id']}/review",
        headers=_STAFF_HEADERS,
        json={"action": "mark_under_review"},
    )
    assert reverted.status_code == 409, reverted.text

    still_scheduled = client.get(
        f"/api/staff/contribute/submissions/{created['submission_id']}",
        headers=_STAFF_HEADERS,
    )
    assert still_scheduled.json()["state"] == "scheduled"

    schedule_item = client.get(f"/api/staff/schedule/{schedule_item_id}", headers=_STAFF_HEADERS)
    assert schedule_item.json()["state"] == "scheduled"


@_FFMPEG_SKIP
def test_concurrent_decline_and_schedule_never_leaves_a_live_row_behind_a_declined_submission(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """BLOCKER race: before this fix, ``decline`` never took the same
    reservation ``accept``/``schedule`` use. A decline could interleave
    between a concurrent schedule request creating its real
    ``civiccast.schedule_items`` row and that same request's own persist --
    the schedule request would create the real row, then read a
    not-yet-declined submission, then lose the persist race to a decline
    that ran in between and had already flipped the JSON state to
    "declined" -- leaving the real row live ("scheduled") behind a
    submission the store called declined. Declined content could still air.

    Reserving decline under the same in-flight ledger accept/schedule use
    closes the interleaving: whichever of the two requests reserves first
    runs to completion BEFORE the other can even begin -- no more window for
    one to read stale state while the other's real side effect is only
    half-applied. This does NOT mean only one request can ever succeed:
    ``decline`` is deliberately still valid against an already-"scheduled"
    submission (that is exactly how a normal, non-racing decline-after-
    schedule cancels a live booking, see
    ``test_decline_after_schedule_cancels_the_real_schedule_item`` above), so
    if schedule wins the reservation first and decline is only refused
    momentarily (409, "another review action is in flight"), the retry-
    shaped request can still land right after and legitimately cancel it --
    both then read 200. What must NEVER happen is the BLOCKER bug: a real
    schedule row created and left live while the JSON store already calls
    the submission "declined". Drives one real concurrent decline request
    against one real concurrent schedule request on the SAME already-
    accepted submission (``threading.Barrier``, same pattern as
    ``test_concurrent_duplicate_accept_produces_exactly_one_library_asset``
    above) and asserts that invariant holds no matter how the two requests
    actually interleave: the submission never ends up "declined" while a
    live (non-cancelled) schedule row still exists for it.
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor

    client = _client(monkeypatch, tmp_path)
    created = client.post(
        "/api/public/contribute/submissions", json=_payload(_real_video(tmp_path))
    ).json()
    accepted = client.post(
        f"/api/staff/contribute/submissions/{created['submission_id']}/review",
        headers=_STAFF_HEADERS,
        json={"action": "accept"},
    )
    assert accepted.status_code == 200, accepted.text
    asset_id = accepted.json()["asset_id"]

    barrier = threading.Barrier(2)
    schedule_request = {
        "action": "schedule",
        "schedule_handoff": {
            "channel_id": "public",
            "requested_start": "2026-06-01T18:00:00Z",
            "duration_seconds": 1800,
        },
    }
    decline_request = {
        "action": "decline",
        "decline_reason": "Racing against a concurrent schedule request.",
    }

    def review_once(payload: dict[str, object]) -> tuple[str, int]:
        barrier.wait()  # release both workers into the review POST together
        response = client.post(
            f"/api/staff/contribute/submissions/{created['submission_id']}/review",
            headers=_STAFF_HEADERS,
            json=payload,
        )
        return str(payload["action"]), response.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(review_once, [schedule_request, decline_request]))

    statuses = [status for _, status in results]
    assert statuses.count(200) >= 1, (
        f"at least one of the racing schedule/decline requests must succeed; results={results}"
    )
    assert all(code in {200, 409} for code in statuses), results

    final = client.get(
        f"/api/staff/contribute/submissions/{created['submission_id']}",
        headers=_STAFF_HEADERS,
    )
    final_state = final.json()["state"]
    assert final_state in {"declined", "scheduled"}, final_state

    schedule_list = client.get("/api/staff/schedule", headers=_STAFF_HEADERS)
    assert schedule_list.status_code == 200
    live_rows_for_asset = [
        item
        for item in schedule_list.json()
        if item["asset_id"] == asset_id and item["state"] != "cancelled"
    ]

    if final_state == "declined":
        assert live_rows_for_asset == [], (
            "the submission ended declined but a live schedule row is still "
            f"behind it: {live_rows_for_asset}"
        )
    else:
        assert len(live_rows_for_asset) == 1, (
            "the submission ended scheduled but no live schedule row backs "
            f"it: {live_rows_for_asset}"
        )


@_FFMPEG_SKIP
def test_schedule_row_is_rolled_back_when_persisting_the_review_fails(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """BLOCKER fix: ``_create_real_schedule_item`` creates a real
    ``civiccast.schedule_items`` row BEFORE ``store.review_submission``
    persists the "scheduled" state. The reservation above closes the
    interleaving that used to let a concurrent decline slip in ahead of
    that persist, but the persist call can still fail for other reasons (a
    store I/O error, a late validation problem) -- if it does after the row
    already exists, that row must not stay live and airable behind a
    submission that never actually reached "scheduled". Forces
    ``review_submission`` to fail exactly on the schedule path, after the
    real row has already been created, and asserts the row was cancelled
    rather than left "scheduled", the submission is not left claiming to be
    scheduled, and the reservation was released so a retry can proceed.
    """
    from civiccast.contribute.store import ContributorStoreError, ContributorSubmissionStore

    client = _client(monkeypatch, tmp_path)
    created = client.post(
        "/api/public/contribute/submissions", json=_payload(_real_video(tmp_path))
    ).json()
    accepted = client.post(
        f"/api/staff/contribute/submissions/{created['submission_id']}/review",
        headers=_STAFF_HEADERS,
        json={"action": "accept"},
    )
    assert accepted.status_code == 200, accepted.text
    asset_id = accepted.json()["asset_id"]

    original_review_submission = ContributorSubmissionStore.review_submission

    def failing_review_submission(self, submission_id, request, **kwargs):  # type: ignore[no-untyped-def]
        if request.action == "schedule":
            raise ContributorStoreError(
                "simulated persist failure after the real schedule row was created"
            )
        return original_review_submission(self, submission_id, request, **kwargs)

    monkeypatch.setattr(ContributorSubmissionStore, "review_submission", failing_review_submission)

    scheduled = client.post(
        f"/api/staff/contribute/submissions/{created['submission_id']}/review",
        headers=_STAFF_HEADERS,
        json={
            "action": "schedule",
            "schedule_handoff": {
                "channel_id": "public",
                "requested_start": "2026-06-01T18:00:00Z",
                "duration_seconds": 1800,
            },
        },
    )
    assert scheduled.status_code == 503, scheduled.text

    still_accepted = client.get(
        f"/api/staff/contribute/submissions/{created['submission_id']}",
        headers=_STAFF_HEADERS,
    )
    assert still_accepted.json()["state"] == "accepted", (
        "a failed persist must not leave the submission claiming to be scheduled"
    )

    schedule_list = client.get("/api/staff/schedule", headers=_STAFF_HEADERS)
    assert schedule_list.status_code == 200
    rows_for_asset = [item for item in schedule_list.json() if item["asset_id"] == asset_id]
    assert len(rows_for_asset) == 1, rows_for_asset
    assert rows_for_asset[0]["state"] == "cancelled", (
        "a real schedule row created before a failed persist must be rolled back "
        f"(cancelled), not left live; got {rows_for_asset[0]}"
    )

    # The reservation must also have been released on this failure path, not
    # leaked -- a subsequent request for the same submission must proceed.
    monkeypatch.setattr(ContributorSubmissionStore, "review_submission", original_review_submission)
    declined = client.post(
        f"/api/staff/contribute/submissions/{created['submission_id']}/review",
        headers=_STAFF_HEADERS,
        json={
            "action": "decline",
            "decline_reason": "Cleaning up after the simulated schedule failure.",
        },
    )
    assert declined.status_code == 200, declined.text
