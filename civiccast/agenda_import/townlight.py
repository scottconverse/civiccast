# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Consume the independent Townlight importer through its versioned JSON CLI."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast

from civiccast.agenda_import.base import (
    AgendaSourceDependencyMissingError,
    AgendaSourceUpstreamError,
)
from civiccast.agenda_import.models import (
    ExternalAgenda,
    ExternalAgendaItem,
    ExternalMeetingSummary,
)


class TownlightSource:
    """Staff-configured source with the standard CivicCast agenda protocol."""

    def __init__(self) -> None:
        self.python = os.environ.get("TOWNLIGHT_IMPORT_PYTHON")
        self.connector = os.environ.get("TOWNLIGHT_IMPORT_CONNECTOR")
        self.input_format = os.environ.get("TOWNLIGHT_IMPORT_FORMAT")
        self.url = os.environ.get("TOWNLIGHT_IMPORT_URL")
        self.capture = os.environ.get("TOWNLIGHT_IMPORT_CAPTURE")

    def _meetings(self) -> list[dict[str, Any]]:
        if not (self.python and self.connector and self.input_format and self.url):
            raise AgendaSourceDependencyMissingError(
                "Set TOWNLIGHT_IMPORT_PYTHON, TOWNLIGHT_IMPORT_CONNECTOR, "
                "TOWNLIGHT_IMPORT_FORMAT and TOWNLIGHT_IMPORT_URL on this station."
            )
        executable = Path(self.python)
        if not executable.is_absolute() or not executable.is_file():
            raise AgendaSourceDependencyMissingError(
                "Set TOWNLIGHT_IMPORT_PYTHON to an absolute executable path."
            )
        command = [self.python, "-m", "townlight_runtime.meeting_imports", self.connector, self.url]
        command += [self.capture] if self.capture else ["--fetch"]
        command += ["--format", self.input_format]
        try:
            # The executable and arguments come from the station environment.
            result = subprocess.run(  # noqa: S603
                command,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                check=False,
                timeout=60,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as error:
            raise AgendaSourceDependencyMissingError(
                "Could not launch the Townlight importer."
            ) from error
        except subprocess.TimeoutExpired as error:
            raise AgendaSourceUpstreamError("The Townlight importer timed out.") from error
        if result.returncode:
            reason = result.stderr.decode("utf-8", errors="replace").strip()
            if "No module named" in reason:
                raise AgendaSourceDependencyMissingError(
                    "Install the Townlight importer in the configured Python."
                )
            raise AgendaSourceUpstreamError(
                reason[:500] or "Townlight did not return a meeting preview."
            )
        try:
            response = json.loads(result.stdout)
        except ValueError as error:
            raise AgendaSourceUpstreamError("Townlight returned invalid JSON.") from error
        if (
            not isinstance(response, dict)
            or response.get("version") != 1
            or not isinstance(response.get("meetings"), list)
            or not all(isinstance(row, dict) for row in response["meetings"])
        ):
            raise AgendaSourceUpstreamError("Townlight returned an unsupported meeting response.")
        return cast(list[dict[str, Any]], response["meetings"])

    @staticmethod
    def _summary(row: dict[str, Any]) -> ExternalMeetingSummary:
        try:
            raw = row.get("scheduled_start") or row.get("source_start")
            when = datetime.fromisoformat(raw) if raw else None
            return ExternalMeetingSummary(
                external_id=row["external_id"],
                title=row["title"],
                meeting_datetime=when,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise AgendaSourceUpstreamError(
                "Townlight returned an invalid meeting entry."
            ) from error

    def fetch_meetings(
        self,
        client_code: str,
        *,
        since: date | None = None,
    ) -> list[ExternalMeetingSummary]:
        results = []
        for row in self._meetings():
            summary = self._summary(row)
            day = summary.meeting_datetime.date() if summary.meeting_datetime else None
            if day is None and row.get("meeting_date"):
                try:
                    day = date.fromisoformat(row["meeting_date"])
                except (TypeError, ValueError) as error:
                    raise AgendaSourceUpstreamError(
                        "Townlight returned an invalid meeting date."
                    ) from error
            if not since or day is None or day >= since:
                results.append(summary)
        return results

    def fetch_agenda(
        self,
        client_code: str,
        event_id: str,
        *,
        archive_year: int | None = None,
    ) -> ExternalAgenda:
        matches = [row for row in self._meetings() if row.get("external_id") == event_id]
        if len(matches) != 1:
            raise AgendaSourceUpstreamError(
                "The selected meeting is no longer in the Townlight source."
            )
        row = matches[0]
        summary = self._summary(row)
        items = row.get("agenda_items")
        if not items:
            raise AgendaSourceUpstreamError(
                "This source has no structured agenda items. Use CivicCast's document import."
            )
        try:
            agenda_items = [
                ExternalAgendaItem(order=index, title=item["title"], doc_url=item.get("url"))
                for index, item in enumerate(items)
            ]
            document_url = next(
                (item["url"] for item in row.get("documents", ()) if item.get("kind") == "agenda"),
                row.get("url"),
            )
            return ExternalAgenda(
                **summary.model_dump(), source_doc_url=document_url, items=agenda_items
            )
        except (KeyError, TypeError, ValueError) as error:
            raise AgendaSourceUpstreamError(
                "Townlight returned an invalid agenda entry."
            ) from error
