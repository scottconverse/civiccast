# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Real setup-boundary app for Playwright.

The browser test exercises the production setup router and app wiring while
adding only local CORS for the Vite preview origin.
"""

from __future__ import annotations

import os

from fastapi.middleware.cors import CORSMiddleware

from civiccast.app import create_app

# The Playwright preview runs on 4174 by default. The verify skill runs its
# own preview on a free port and names it here so it never collides with a
# preview another checkout left on 4174.
_PREVIEW_ORIGINS = ["http://localhost:4174", "http://127.0.0.1:4174"]
_EXTRA_ORIGIN = os.environ.get("CIVICCAST_E2E_PREVIEW_ORIGIN", "").strip()
if _EXTRA_ORIGIN:
    _PREVIEW_ORIGINS.append(_EXTRA_ORIGIN)

app = create_app()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_PREVIEW_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
