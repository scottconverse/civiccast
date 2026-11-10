# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Durable, non-secret agenda provider choices; credentials never enter this file."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from threading import RLock
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from civiccast.agenda_import.config import (
    AgendaImportSettings,
    JsPortalVendorHint,
    validate_client_code,
    validate_portal_url,
)


class AgendaSourceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Literal["off", "legistar", "primegov", "civicclerk", "js_portal", "townlight"] = "off"
    client_code: str | None = None
    portal_url: str | None = None
    portal_vendor_hint: JsPortalVendorHint = "generic"
    timeout_seconds: float = Field(default=10.0, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_provider(self) -> AgendaSourceSettings:
        if self.client_code:
            validate_client_code(self.client_code)
        if self.portal_url:
            validate_portal_url(self.portal_url)
        return self

    @classmethod
    def defaults(cls) -> AgendaSourceSettings:
        settings = AgendaImportSettings.from_env()
        # Legacy enabled installs need not have a default tenant; keep discovery usable.
        return cls.model_validate(
            {
                "source": settings.source,
                "client_code": settings.client_code,
                "timeout_seconds": settings.timeout_seconds,
            }
        )


class AgendaSourceSettingsUpdate(AgendaSourceSettings):
    @model_validator(mode="after")
    def require_enabled_provider_fields(self) -> AgendaSourceSettingsUpdate:
        if self.source != "off" and not self.client_code:
            raise ValueError("Enter the tenant / site code before enabling agenda import.")
        if self.source == "js_portal" and not self.portal_url:
            raise ValueError("Enter the public portal URL for the JS-rendered portal.")
        return self


class AgendaSourceSettingsStore:
    """Atomic JSON store following the recorded-caption station settings pattern."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = RLock()

    def load(self) -> AgendaSourceSettings:
        with self._lock:
            if not self.path.exists():
                return AgendaSourceSettings.defaults()
            return AgendaSourceSettings.model_validate_json(self.path.read_text(encoding="utf-8"))

    def save(self, settings: AgendaSourceSettings) -> bool:
        with self._lock:
            if self.path.exists() and self.load() == settings:
                return False
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    newline="\n",
                    prefix=f".{self.path.name}.",
                    suffix=".tmp",
                    dir=self.path.parent,
                    delete=False,
                ) as stream:
                    temporary_path = Path(stream.name)
                    stream.write(json.dumps(settings.model_dump(mode="json"), indent=2) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                if os.name != "nt":
                    temporary_path.chmod(0o600)
                temporary_path.replace(self.path)
                return True
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)


def default_agenda_import_settings_path() -> Path:
    from civiccast.installer.station_state import station_state_path

    return station_state_path().with_name("agenda-import-settings.json")
