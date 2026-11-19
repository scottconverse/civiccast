#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Build the signed native ``native-server-binaries`` component pack
(PostgreSQL 17 + a TSDuck subset) that
``civiccast.native.provision.pack.verify_server_binaries_pack`` verifies and
``civiccast.native.provision.__main__.resolve_provision_paths`` expects to
find, extracted, at ``<install_root>\\packs\\native-server-binaries\\
payload\\bin\\initdb.exe``.

WP2 gap this closes: the provisioning engine (wired at 3907465a) has a real,
tested caller (``python -m civiccast.native.provision``) and a real trust
wire (``civiccast.native.provision.pack``), but nothing ever built the pack
that trust wire verifies -- ``nsis-hooks-native.nsh``'s own comment names
this exact gap ("Server-binaries pack staging ... no earlier work package
lays this hook's own file section down with a packs\\ tree"). This script is
that builder.

Mirrors ``scripts/build_native_caption_pack.py`` + ``civiccast.installer.
native_packs`` conventions exactly: pinned-input validation before packing,
a signed ZIP64 pack via ``build_native_pack``, and a development-signing-key
guard. Two things caption packs do NOT need that this pack does:

* **Acquisition.** Caption models are pulled by a separate, already-landed
  flow. PostgreSQL/TSDuck are not -- ``--acquire`` (this script) reuses
  ``scripts.provision_native_runtime_dependencies``'s ALREADY-REVIEWED lock
  (``native-windows-runtime-dependencies.lock.json``, schema_version 2,
  pinned PostgreSQL 17.10-2 / TSDuck 3.44-4676 URLs +
  SHA-256) and its ``fetch_locked_artifact``/``safe_extract_zip`` primitives
  verbatim -- never re-typing a URL or a hash this repo already reviewed.
  Only the 2 artifacts this pack needs are fetched (never the ffmpeg/node/
  ollama artifacts the SAME lock also pins for the unrelated "Core" pack --
  see ``build_native_distribution.py``, a different pack with a different
  component identity, "core", not "native-server-binaries").
* **Minimization.** The upstream PostgreSQL/TSDuck distributions are
  general-purpose (pgAdmin-adjacent StackBuilder GUI, wx* libs, ecpg,
  pgbench, 87 TSDuck plugins covering satellite/cable/DVB hardware this
  product never touches, ...). This builder ships only the closure real
  code in this repo actually invokes -- see ``POSTGRES_BIN_PINS`` /
  ``TSDUCK_BIN_PINS`` below for the exact evidence per file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Final

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT: Final[Path] = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from civiccast._native_version import __version__  # noqa: E402
from civiccast.installer.native_packs import build_native_pack  # noqa: E402
from civiccast.native.provision.pack import (  # noqa: E402
    SERVER_BINARIES_COMPONENT,
)
from civiccast.native.provision.seams import initdb_argv  # noqa: E402
from civiccast.native.runtime_licenses import (  # noqa: E402
    classify_server_pack_file,
    is_gpl_license,
)
from scripts.native_openssl_overlay import (  # noqa: E402
    secured_postgres_sources as openssl_postgres_sources,
)
from scripts.native_security_overlay import (  # noqa: E402
    secured_postgres_sources as library_postgres_sources,
)
from scripts.provision_native_runtime_dependencies import (  # noqa: E402
    LOCK_PATH,
    fetch_locked_artifact,
    load_lock,
    safe_extract_zip,
)


def secured_postgres_sources(sources: dict[str, Path], cache: Path) -> dict[str, Path]:
    return library_postgres_sources(openssl_postgres_sources(sources, cache), cache)


_REPARSE_POINT: Final[int] = 0x400
#: Upstream component versions this builder was reviewed against -- SAME
#: pins as ``native-windows-runtime-dependencies.lock.json``'s ``postgres``/
#: ``tsduck`` entries (``--acquire`` fetches exactly those). Not
#: re-declared as a second source of truth: ``acquire_server_pack_sources``
#: asserts the loaded lock's ``version`` fields equal these before fetching,
#: so a lock file edited out from under this builder fails loud instead of
#: silently shipping an unreviewed version.
POSTGRES_VERSION: Final[str] = "17.12-1"
TSDUCK_VERSION: Final[str] = "3.44-4676"


class ServerPackBuildError(RuntimeError):
    """The native-server-binaries pack could not be built."""


# ---------------------------------------------------------------------------
# Pinned minimal-closure inventories.
#
# Each dict maps a SOURCE-relative filename (directly under the artifact's
# extracted root, or a named subdirectory) to (expected_bytes, expected_
# sha256) -- computed directly from the reviewed, hash-pinned upstream
# archives (see the lock file + this task's evidence file for the exact
# derivation commands). Executables and DLLs are pinned individually here
# (the supply-chain-meaningful subset, mirroring
# ``build_native_caption_pack.WHISPER_MODEL_FILES``'s per-file pin style);
# the bulk data trees (``share/timezone/``, ``share/timezonesets/``, the
# ``btree_gist`` SQL migration scripts) are validated by exact PATH-SET
# instead (no extra/missing file), not per-file hash -- both are already
# covered transitively by ``fetch_locked_artifact``'s whole-archive SHA-256
# check before ANY of these bytes are ever extracted, so per-file hashing
# of hundreds of small SQL/data files would duplicate, not add, a trust
# boundary. A tamper AFTER extraction (not the download itself) is still
# caught: ``build_native_pack`` hashes every byte it packs into the
# manifest it signs, and ``verify_native_pack`` re-derives every hash on
# read.
# ---------------------------------------------------------------------------

#: PostgreSQL executables this product's provisioning engine
#: (``civiccast.native.provision``, ``initdb``/``postgres``/``pg_ctl``) and
#: disaster-recovery tooling (``civiccast/dr/backup.py``,
#: ``civiccast/dr/restore_drill.py`` -- ``pg_dump`` for the per-database
#: snapshot, ``pg_dumpall --globals-only`` for cluster-global roles,
#: ``pg_restore`` for restore) and operator diagnostics (``psql``) actually
#: invoke. Excludes: StackBuilder (GUI installer for extra modules this
#: product never offers), clusterdb/createdb/createuser/dropdb/dropuser/
#: reindexdb/vacuumdb/vacuumlo/oid2name (interactive DBA utilities nothing
#: in this repo calls), ecpg (embedded-SQL C preprocessor -- a build tool,
#: not a runtime), pgbench (benchmarking), pg_amcheck/pg_basebackup/
#: pg_checksums/pg_combinebackup/pg_createsubscriber/pg_isready/
#: pg_receivewal/pg_recvlogical/pg_resetwal/pg_rewind/pg_test_fsync/
#: pg_test_timing/pg_upgrade/pg_verifybackup/pg_waldump/pg_walsummary/
#: pg_archivecleanup/pg_controldata/pg_config (operational tools for
#: replication/upgrade/backup-orchestration topologies this product does
#: not run today -- a later work package that adds them re-derives their
#: pins the same way this file does, never inherits an unreviewed default).
POSTGRES_BIN_PINS: Final[dict[str, tuple[int, str]]] = {
    "initdb.exe": (255608, "515eeba9d255c60b4a0eb2954d9b79f39f097694846e5df5a9bf308c578f1d8f"),
    "postgres.exe": (9996920, "70334b2428dac81d111b4e3a6cfed01568b216e4ef3c5cc879d423a0fe69c0df"),
    "pg_ctl.exe": (142456, "833ab137ed6df021e49dfbef062783be5c1e898119f43e01440373a88a6ab998"),
    "pg_dump.exe": (626808, "64539be632194750c9846f3a22056ffd15500310d9aefae6a46fc5b48fdd9928"),
    "pg_dumpall.exe": (204920, "1bfa7b5711aa3faaeb833ece3ad21036ad338cf54cbf98a433a7f0d6336118bf"),
    "pg_restore.exe": (383608, "ea7e1d1855f720d0593cb1683b38e12e215dd26302decb2759aaf7972bc52382"),
    "psql.exe": (645752, "defe034e42d095afead17d91619e202a851b2e66f2c2fca1eb29123059868197"),
}

#: The bundled runtime DLLs the seven binaries above actually import,
#: determined by a real ``pefile``-based recursive PE import-table walk
#: (ordinary + delay-load directories, same method ``scripts/
#: build_native_runtime_closure.py`` uses for the media closure) against
#: the extracted, hash-verified archive -- never assumed from the full
#: ``bin/`` listing. ``pg_dumpall.exe`` re-checked separately when it was
#: added and introduces no DLL beyond this same set. Excludes libcurl/
#: libecpg*/libpgtypes/libxslt/wx*/testplug.dll/pgevent -- present in the
#: upstream ``bin/`` tree but not imported by any of the selected
#: executables.
POSTGRES_BIN_DLL_PINS: Final[dict[str, tuple[int, str]]] = {
    "icudt67.dll": (28409464, "557b3b2e18839348a916363f334112f3d46033940474990c908c71b2e0651f8a"),
    "icuin67.dll": (2684536, "0e9e71043b11122dc1a940e73964f8df27c0c916499db9a6534fbaaa22888d1a"),
    "icuuc67.dll": (1917048, "a0385f6e04d5b4b0f8c30a3669cf5e56acd8a88022060c789167645c92320c5b"),
    "libcrypto-3-x64.dll": (
        5496952,
        "8efe1edb434ea9f44f1fbb9be38374857e33666d4aab058a6413e6db566f1baf",
    ),
    "libiconv-2.dll": (
        1430176,
        "6fce90bc8ea4b730e897e8d5bcd4cbb1e83a7f5083e1d2f82acde8c49b1ea41f",
    ),
    "libintl-9.dll": (457336, "87fbcee976b4b051e0f3f11a6a2fd5613b42ddb2b97716d069942828f2840d13"),
    "liblz4.dll": (150648, "72f8fcb62128bc52cc9b679f27b2517740f0898f7a4da5400ea9206e8b4d771f"),
    "libpq.dll": (361592, "d87674d536cb8fcc4897f8d6a1c8445675e33447ca0e6ebac210559029f19d04"),
    "libssl-3-x64.dll": (
        1303160,
        "45a45bc89cd4c6621f84da60356c64a69140bf61b027cc9858c36ea37d7f74c1",
    ),
    "libxml2.dll": (1224312, "1005720b49584d5963b2e57dd3e36ee65e290ef505e558f25f4bf8962c5dabb6"),
    "libzstd.dll": (737400, "a2c58d2cbca1feaab58cfb36e895ce2671f9658a251d865296b6fadf099de88c"),
    "zlib1.dll": (103544, "ea2d0c0c78d0a62333ec616882885d05482ad5fd086c1360bc3611a8c71b992a"),
}

#: ``lib/`` selection, in three groups. LESSON (Sandbox matrix row 1 run 2,
#: 2026-07-30): ``lib/`` is NOT only contrib extensions -- it also holds
#: CORE runtime modules the server bootstrap itself loads. The original
#: btree_gist-only selection made the first live ``initdb`` die with
#: ``FATAL: could not access file "$libdir/utf8_and_win"``.
#:
#: 1. The complete encoding-conversion module family (every ``*_and_*.dll``
#:    plus ``euc2004_sjis2004.dll``): ``initdb``'s bootstrap and any
#:    client-encoding negotiation load these on demand; shipping the whole
#:    24-module family (~2 MB) rather than guessing which encodings a
#:    station will ever negotiate.
#: 2. Core PL / dictionary modules: ``plpgsql.dll`` (the default procedural
#:    language, installed by ``initdb``'s bootstrap itself) and
#:    ``dict_snowball.dll`` (loaded by the ``snowball_create.sql`` this
#:    pack already ships in ``share/``).
#: 3. The one loadable extension the product's schema actually installs:
#:    ``civiccast/schedule/migrations/versions/
#:    0003_create_schedule_items_table.py`` runs ``CREATE EXTENSION IF NOT
#:    EXISTS btree_gist`` (grepped every ``migrations/`` tree in the repo --
#:    the only ``CREATE EXTENSION`` statement that exists). Every other
#:    ``lib/*.dll`` contrib extension upstream ships remains excluded.
#:
#: The build-time bootstrap proof (``_prove_postgres_bootstrap``, CLI path)
#: runs a REAL ``initdb`` + server start + ``CREATE EXTENSION btree_gist``
#: + ``to_tsvector`` against exactly this selection on every pack build, so
#: the NEXT missing file fails the build -- never a station install.
POSTGRES_LIB_PINS: Final[dict[str, tuple[int, str]]] = {
    "cyrillic_and_mic.dll": (
        30840,
        "2f04852acd519dad505374b39e339ffcb2c8eefdafd0132cb5bd259ddde220e3",
    ),
    "euc2004_sjis2004.dll": (
        27256,
        "7ebdf90b12bfeb87dffb9e6f6e7b0e37f3fd05c35c2718477265fc1883dfb067",
    ),
    "euc_cn_and_mic.dll": (
        26232,
        "2801c61cac0478ae83f5d75b4bd649cea22e9baadafc0caabbd67882042d18bb",
    ),
    "euc_jp_and_sjis.dll": (
        33400,
        "557215eabf6c2b8febff3d9b7b8832eb2e99193cfaa4234e9f6ae50f3f620834",
    ),
    "euc_kr_and_mic.dll": (
        26232,
        "50828febfce82607a93c7bb2fc82b3de7a526cdcecd07818c0d8afc98c8726fb",
    ),
    "euc_tw_and_big5.dll": (
        32888,
        "d5b88992d06ba77cbce3456fc8d800252e169515eb2359a509f35594054e9739",
    ),
    "latin2_and_win1250.dll": (
        26744,
        "965830efb5bff594cee56aee9438b167ac3bbf94f8790018a81d3e0720d06efe",
    ),
    "latin_and_mic.dll": (
        26232,
        "4e0986724174ac7755e826bf6d3ad17f69553c3aecfc4d81007b4e17a38eef0f",
    ),
    "utf8_and_big5.dll": (
        139384,
        "6f5eec40e0942f06c70ebaeb8966594e1d06340878045285b22209352ffdb78f",
    ),
    "utf8_and_cyrillic.dll": (
        30840,
        "18d82c2988cd78e11fadf8f427339e834e5be22e3ff5f21029267cefbe81b884",
    ),
    "utf8_and_euc2004.dll": (
        229496,
        "ca925d6d6d9c8fc8e2d4400072e1fe36a1721d77e359ac089754de5691aa4721",
    ),
    "utf8_and_euc_cn.dll": (
        99960,
        "68816b9010769c52a1bec569e52fc7b24a26eec901389680a449d8d737246f9c",
    ),
    "utf8_and_euc_jp.dll": (
        175736,
        "7b15c2e26cdbc9cc834fae1051e1ca2541c871d65ab7bb54b0eaff1c3a4d6c92",
    ),
    "utf8_and_euc_kr.dll": (
        127608,
        "5192a2ac9611f1757a95d0bddab1382950ff4bc7811e8b6e82f5e91fd05bc87b",
    ),
    "utf8_and_euc_tw.dll": (
        224376,
        "3965b3532c0ab7bf2bed7bda8b5af9f8c1ddc17485265f48e1e727084523a28a",
    ),
    "utf8_and_gb18030.dll": (
        285816,
        "ff88901b61ab484a643f6d85acd67492ab8c5f91cc0f48beb0cf8b37ff3bc1d9",
    ),
    "utf8_and_gbk.dll": (
        171128,
        "bd208af2c24759bae1067d80c0fc20221d1d841a8616d10d8ced052cad070f4b",
    ),
    "utf8_and_iso8859.dll": (
        47224,
        "2fd789cd2d16cc010e09948e209344e5bc4a1132df73dc8087948670eeff378f",
    ),
    "utf8_and_iso8859_1.dll": (
        25720,
        "7ee1550e92396c3bd3802753da96a00a241e229f85bf2030f7f343c279d6da4c",
    ),
    "utf8_and_johab.dll": (
        186488,
        "64ab8092e2732c4fec527cff2f1f531b001326dc2a4137b3d3d8183e7b3ef402",
    ),
    "utf8_and_sjis.dll": (
        106104,
        "eb68fe2dc454d6a06afea1dbc03ebafe338ea90551078693d437981b8d5c62d3",
    ),
    "utf8_and_sjis2004.dll": (
        151160,
        "abc5ab45afe2368454e9abde6c7bf6cc13ee0c90a872e87a975dd7217e0509b2",
    ),
    "utf8_and_uhc.dll": (
        192120,
        "b78db6be8e6a1300f839264b1a2d9c770c0ac520f910975fee125d28476a557b",
    ),
    "utf8_and_win.dll": (
        50296,
        "5fae3c5e9fab3cb213c4388895aafda9a29242abf8026266710382057d8123bf",
    ),
    "plpgsql.dll": (206456, "bf5c3e23bd344275e44ae706ad61ac735b1630444311cde4ce448206bda7f50b"),
    "dict_snowball.dll": (
        659064,
        "2ed769517dff0cfb14b17c35de32a9b1480fee3fadefaee21f555aabdf1ed372",
    ),
    "btree_gist.dll": (90744, "0471897c44f0713cbec0646e466184859c0900a72a356d02302b9add28ae314f"),
}

#: ``initdb`` reads the bootstrap catalog + SQL scripts and copies the
#: ``.sample`` config templates from ``share/`` at cluster-creation time;
#: PostgreSQL refuses to start without a valid ``share/timezone``` +
#: ``share/timezonesets`` (the ``timezone``/``timezone_abbreviations`` GUCs'
#: backing data). ``share/locale`` (~23 MB of ``.mo`` gettext translation
#: catalogs -- purely cosmetic: translated log/error text) and ``share/doc``
#: are excluded; their absence changes nothing about correctness, only the
#: language server log lines appear in (English, the untranslated default).
POSTGRES_SHARE_TOP_FILES: Final[tuple[str, ...]] = (
    "postgres.bki",
    "errcodes.txt",
    "information_schema.sql",
    "snowball_create.sql",
    "sql_features.txt",
    "system_constraints.sql",
    "system_functions.sql",
    "system_views.sql",
    "pg_hba.conf.sample",
    "pg_ident.conf.sample",
    "pg_service.conf.sample",
    "postgresql.conf.sample",
    "psqlrc.sample",
)

#: ``btree_gist``'s own ``.control`` + every version-upgrade ``.sql`` script
#: PostgreSQL's extension machinery may need (``CREATE EXTENSION`` always
#: installs the latest version file; a later ``ALTER EXTENSION ... UPDATE``
#: needs the upgrade-path scripts between versions -- shipping all of them,
#: same as upstream, rather than guessing which single version is "enough").
POSTGRES_SHARE_EXTENSION_FILES: Final[tuple[str, ...]] = (
    # plpgsql: initdb's own post-bootstrap initialization runs
    # `CREATE EXTENSION plpgsql` -- caught live by the build-time bootstrap
    # proof the first time it ran (the DLL alone is not enough; the
    # extension control + install script must ship too).
    "plpgsql.control",
    "plpgsql--1.0.sql",
    "btree_gist.control",
    "btree_gist--1.2.sql",
    "btree_gist--1.2--1.3.sql",
    "btree_gist--1.3--1.4.sql",
    "btree_gist--1.4--1.5.sql",
    "btree_gist--1.5--1.6.sql",
    "btree_gist--1.6--1.7.sql",
    "btree_gist--1.0--1.1.sql",
    "btree_gist--1.1--1.2.sql",
)

#: ``tsp.exe`` + the exact plugin closure ``civiccast/egress/ts_relay.py``
#: (``continuity``, ``pcradjust``), ``civiccast/egress/compliance.py``
#: (``until``, ``analyze``), and ``civiccast/alerting/self_test.py``
#: (``analyze``) invoke, determined the SAME way as the PostgreSQL DLL set
#: above: a real recursive PE import-table walk of ``tsp.exe`` PLUS all 4
#: plugin DLLs resolves to exactly ``tscore.dll`` + ``tsduck.dll`` (TSDuck's
#: core libraries) and only OS-provided imports (QUARTZ/WININET/WINUSB/
#: WinSCard) -- never ``libsrt``/``librist``/``libvatek``/DTAPI (TSDuck's
#: OWN ``OTHERS.txt`` third-party notices), which back only the
#: ``tsplugin_srt``/``tsplugin_rist``/``tsplugin_dektec``/``tsplugin_vatek``
#: plugin DLLs this pack does not ship. ``ip``, ``file``, and ``drop`` (also
#: used by the same call sites) need no separate plugin DLL of their own:
#: TSDuck compiles those three fundamental I/O plugins directly into
#: ``tsduck.dll``/``tscore.dll`` (confirmed: no ``tsplugin_ip.dll`` /
#: ``tsplugin_file.dll`` / ``tsplugin_drop.dll`` exists in the upstream
#: ``bin/`` listing at all, out of 87 ``tsplugin_*.dll`` files). Of the 87
#: shipped plugins, only these 4 leave this pack; the other 83 (satellite/
#: cable/ATSC/ISDB tuners, Dektec/VATek/HiDes hardware modulators, DVB
#: descramblers, SRT/RIST/SMPTE-2022/FLUTE transports, ...) are excluded.
TSDUCK_BIN_PINS: Final[dict[str, tuple[int, str]]] = {
    "tsp.exe": (88_576, "3ec0e60c1fe0459ba3baef70ac2527321841d0c84a82d0bea153009b51fe4a0d"),
    "tscore.dll": (
        1_990_656,
        "21ded43ac3d0c9954edf3ba9f2a3179bbf38d995c5655b79606c3f99405ace70",
    ),
    "tsduck.dll": (
        11_967_488,
        "76e1186f30aa4d04fdc492e0cafd106a8075a7492df3369512d1222beb03c083",
    ),
    "tsplugin_analyze.dll": (
        111_616,
        "f91a2de5160966ab37383033d7cb020d3a26e218b3e8f3103406b223d0da45f2",
    ),
    "tsplugin_continuity.dll": (
        91_136,
        "44860daa02ebf2503756486c9b59b8d3f44b9962fe1f65415531de66a7445991",
    ),
    "tsplugin_pcradjust.dll": (
        105_472,
        "74b03d91336b890c921da32c26d1136da904f984b769a40ffab843b49c4231ec",
    ),
    "tsplugin_until.dll": (
        93_696,
        "787650f04a4fc18c6e9c6b86aecf9c2181f425ec9725a7c89ea00119ebc09e0d",
    ),
}

#: TSDuck resolves its own ``.names``/``.xml`` configuration/data files
#: RELATIVE TO ``tsp.exe``'s own directory on Windows -- this is every such
#: file the upstream ``TSDuck-Win64-3.44-4676-Portable.zip`` ships directly
#: in ``bin/`` (the complete non-exe/non-dll file set at that level; none
#: live in a separate ``share``/``etc`` tree in this archive). Enumerated by
#: extracting the SAME hash-pinned archive
#: ``native-windows-runtime-dependencies.lock.json`` already reviews
#: (``sha256=b0ca0f96...``) and listing ``bin/`` directly -- never guessed.
#: Gate A run 2026-09-03 22:55Z failed the TS-capture proof
#: (``sandbox-lab/scripts/In-Sandbox-Report.ps1``'s ``Test-TsProof``, which
#: runs ``tsp -I ip ... -P until -P continuity -P analyze``) with
#: ``Error: configuration file 'dtv' not found`` / ``file not found:
#: tsduck.hfbands.xml`` -- this pack shipped tsp.exe and the 4 plugin DLLs
#: but none of the data files tsp/tscore/tsduck.dll load at runtime to
#: resolve service ids, DVB tables, HF frequency bands, IP/OUI vendor
#: lookups, and the monitor/time XML schemas the ``analyze``/``continuity``/
#: ``until`` plugins and the core ``ip``/``file``/``drop`` I/O (compiled into
#: tsduck.dll/tscore.dll, see TSDUCK_BIN_PINS' comment) depend on. Shipping
#: the complete small (~1 MB total) config-file set from ``bin/`` -- rather
#: than guessing which subset the 4 pinned plugins individually touch -- is
#: the same minimization posture as POSTGRES_SHARE_DATA_DIRS' path-SET
#: validation: these are TSDuck's own bundled data, not extra plugin
#: binaries (no plugin DLL beyond the 7 already pinned above is added).
TSDUCK_DATA_PINS: Final[dict[str, tuple[int, str]]] = {
    "tscore.ip.names": (
        8_252,
        "a973cee5ca0ad4e359215ad7d40d916b14e178b61d26b37bbbd1be27dfc8cbff",
    ),
    "tscore.keytable.model.xml": (
        446,
        "808654a5b66c1845812b9f9c4b4c754643b6c4195f854bc93bdb4d947f6c7e65",
    ),
    "tscore.monitor.model.xml": (
        934,
        "613e706ab1a7901472a494e8741e7c2f1dac8fdedd3bc85c4f1cb3d266741d4f",
    ),
    "tscore.monitor.xml": (
        1_316,
        "a43bf7a65352b57889c6f71e0bda17d248a41bfc64878ebe7bcae3109b68bdab",
    ),
    "tscore.time.model.xml": (
        535,
        "a73e32768dfd91ed1104854684f99ef1000bb03b8da3580e42db78ded55b9595",
    ),
    "tscore.time.xml": (
        2_190,
        "57a1263802f3f2ce9a43e98a6299e6bc140f3ba66ea43db6ff2c2a79b96fd57a",
    ),
    "tsduck.channels.model.xml": (
        3_890,
        "85f7eda1da636e1240b1806a5ee68ae90eae181f6778b39aa3e7a3b3228a0b94",
    ),
    "tsduck.dektec.names": (
        6_370,
        "8395a8c06229f98f3137fcbd4ce69b3def76bd6384b5b96f133793e473a67406",
    ),
    "tsduck.dtv.names": (
        181_385,
        "95572389459a3993155aac1f57929a535c670cd8181b79e3db9ae77d74d32262",
    ),
    "tsduck.etuner.model.xml": (
        1_388,
        "d146bac07c06fb1dd0abd40c4b115f590ef8868b38b9a3e4862fd0435beb79fd",
    ),
    "tsduck.hfbands.model.xml": (
        1_089,
        "4341fc46f7617ed5e8a22a5f9a7b919497cd5346a9f593f73307ae6c25315365",
    ),
    "tsduck.hfbands.xml": (
        5_104,
        "4d507ecbf7a2b1ced56ce99ba19f7ee90d0093f3501dbef4ecdff7ffd7a69d4f",
    ),
    "tsduck.hides.names": (
        8_084,
        "6a91e636fb6e1b1e0d5484c7199d996c104494281fc9e73cf38fe0dd9b9af308",
    ),
    "tsduck.lnbs.model.xml": (
        882,
        "ce3787479ee8648131c3df406fa1efbdd4616791f61dde60e99c25f145a9f6c0",
    ),
    "tsduck.lnbs.xml": (
        5_634,
        "c0533c0a5e848f60c596e4bc253494f29863d995125ef75796137f5f8fc00833",
    ),
    "tsduck.oui.names": (
        666_221,
        "686be47ed82ace7dc2e55c528d887d82a8aa1bf0a1944a687541cadf3bdb3c97",
    ),
    "tsduck.tables.model.xml": (
        137_620,
        "698b93000b79387f23a45dd2d457a812df9fc7a871f069b68f4003dab1c6bc42",
    ),
}

#: Required upstream license text, one per component, verified present at
#: the exact archive-relative path named. Not hash-pinned individually
#: (license TEXT changing is not a supply-chain risk the way binary bytes
#: changing is; presence + content is instead checked via
#: ``classify_server_pack_file``, which requires every packed path resolve
#: to a confirmed SPDX id) but presence is still a hard requirement -- a
#: missing license file refuses the build the same as a missing binary.
POSTGRES_LICENSE_FILES: Final[tuple[str, ...]] = (
    "server_license.txt",
    "commandlinetools_3rd_party_licenses.txt",
)
TSDUCK_LICENSE_FILES: Final[tuple[str, ...]] = ("LICENSE.txt", "OTHERS.txt")

#: PostgreSQL's bundled IANA time zone database directories, plus
#: ``tsearch_data`` (85 KB: stop-word lists + ispell/synonym samples the
#: text-search dictionaries read at QUERY time). ``tsearch_data`` earned its
#: place via the build-time bootstrap proof: ``snowball_create.sql`` (shipped
#: in ``share/``) installs the ``english`` text-search configuration into
#: every cluster at initdb, so any query touching it (``to_tsvector`` --
#: exercised by the proof's probe) errors without these files even though no
#: product code calls it today; shipping the catalog without its data would
#: be a fail-at-runtime landmine. All validated by exact path SET (every
#: file present, nothing extra), not per-file pin; see the module
#: docstring's "Minimization"/pinning-strategy note.
POSTGRES_SHARE_DATA_DIRS: Final[tuple[str, ...]] = (
    "timezone",
    "timezonesets",
    "tsearch_data",
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _require_regular_file(path: Path, *, label: str) -> Path:
    try:
        details = path.lstat()
    except OSError as exc:
        raise ServerPackBuildError(f"{label} is missing: {path}") from exc
    attributes = int(getattr(details, "st_file_attributes", 0))
    if not stat.S_ISREG(details.st_mode) or path.is_symlink() or attributes & _REPARSE_POINT:
        raise ServerPackBuildError(f"{label} must be a regular non-reparse file: {path}")
    return path


def _require_real_directory(path: Path, *, label: str) -> Path:
    path = path.expanduser().resolve()
    try:
        details = path.lstat()
    except OSError as exc:
        raise ServerPackBuildError(f"{label} is missing: {path}") from exc
    attributes = int(getattr(details, "st_file_attributes", 0))
    if not stat.S_ISDIR(details.st_mode) or path.is_symlink() or attributes & _REPARSE_POINT:
        raise ServerPackBuildError(
            f"{label} must be a real directory, not a link or reparse point: {path}"
        )
    return path


def _validate_pinned_file(
    path: Path, *, expected_bytes: int, expected_sha256: str, label: str
) -> None:
    path = _require_regular_file(path, label=label)
    data = path.read_bytes()
    if len(data) != expected_bytes:
        raise ServerPackBuildError(
            f"{label} byte length mismatch: expected {expected_bytes}, observed {len(data)}"
        )
    observed = _sha256_bytes(data)
    if observed != expected_sha256:
        raise ServerPackBuildError(
            f"{label} SHA-256 mismatch: expected {expected_sha256}, observed {observed}"
        )


def _collect_data_tree(root: Path, *, dest_prefix: str) -> dict[str, Path]:
    """Every regular file under ``root`` -> its ``dest_prefix``-relative
    pack destination. Refuses symlinks, reparse points, and anything that
    is not a plain file (the same posture ``native_packs``'s own source
    validation applies at pack-build time, applied here too so a
    traversal/link problem is caught at the SELECTION stage with a clear
    per-file message, not a generic one from deep inside ``build_native_pack``).
    """

    root = _require_real_directory(root, label=f"{dest_prefix} data tree")
    sources: dict[str, Path] = {}
    for candidate in sorted(root.rglob("*")):
        if candidate.is_dir():
            continue
        details = candidate.lstat()
        attributes = int(getattr(details, "st_file_attributes", 0))
        if candidate.is_symlink() or attributes & _REPARSE_POINT:
            raise ServerPackBuildError(
                f"{dest_prefix} data tree contains a link or reparse point: {candidate}"
            )
        if not stat.S_ISREG(details.st_mode):
            raise ServerPackBuildError(
                f"{dest_prefix} data tree contains a non-regular file: {candidate}"
            )
        relative = PurePosixPath(dest_prefix) / candidate.relative_to(root).as_posix()
        sources[relative.as_posix()] = candidate
    if not sources:
        raise ServerPackBuildError(f"{dest_prefix} data tree is empty: {root}")
    return sources


def require_allowed_signing_key(key_id: str, *, allow_development_key: bool) -> None:
    """Keep development trust roots out of an accidental release build
    (same contract as ``build_native_caption_pack``'s guard)."""

    if key_id.startswith("development-") and not allow_development_key:
        raise ServerPackBuildError(
            "development pack signing keys require --allow-development-key; "
            "release packaging must use Scott-approved production key custody"
        )


def load_ed25519_private_key(path: Path) -> Ed25519PrivateKey:
    if not path.is_file():
        raise ServerPackBuildError(f"pack signing private key is missing: {path}")
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ServerPackBuildError("pack signing private key must be Ed25519")
    return key


# ---------------------------------------------------------------------------
# Acquisition (--acquire): reuse the reviewed lock + primitives verbatim.
# ---------------------------------------------------------------------------

_ACQUIRE_ARTIFACTS: Final[tuple[str, ...]] = ("postgres", "tsduck")
_ACQUIRE_EXPECTED_VERSIONS: Final[dict[str, str]] = {
    "postgres": POSTGRES_VERSION,
    "tsduck": TSDUCK_VERSION,
}


def acquire_server_pack_sources(cache: Path, *, lock_path: Path = LOCK_PATH) -> dict[str, Path]:
    """Download + verify + extract ONLY the postgres/tsduck artifacts
    from the reviewed runtime-dependency lock (never the ffmpeg/node/ollama
    artifacts the same lock also pins for the unrelated "Core" pack).

    ``cache`` is caller-controlled and MUST live outside the repository
    (this task's hard rule) -- callers pass a scratch/temp directory, never
    a path under the checked-out tree.

    A pre-existing ``cache/extracted/<name>`` is trusted only if it is
    actually COMPLETE -- re-verified against the same pinned bin/lib/share
    file set ``build_server_pack()`` itself requires (see
    ``_extracted_tree_is_complete``), not merely present. Candidate run
    32845198987 failed identically in both attempts with "pinned PostgreSQL
    initdb.exe is missing" from a persistent self-hosted `--cache` whose
    extraction had been interrupted by a prior run; a bare `destination.
    exists()` check trusted the incomplete tree and never re-extracted it.
    An invalid tree is cleared and re-extracted from the ALREADY hash-
    verified archive (``fetch_locked_artifact``'s own `.partial`-then-
    verify-then-rename download is unaffected by any of this -- the
    archive on disk was never the problem, only the derived extraction).
    """

    lock = load_lock(lock_path)
    extracted: dict[str, Path] = {}
    for name in _ACQUIRE_ARTIFACTS:
        artifact = lock["artifacts"][name]
        expected_version = _ACQUIRE_EXPECTED_VERSIONS[name]
        if str(artifact["version"]) != expected_version:
            raise ServerPackBuildError(
                f"{name} artifact version drifted from this builder's reviewed pin: "
                f"lock has {artifact['version']!r}, builder expects {expected_version!r} "
                "-- re-review before rebuilding"
            )
        archive = fetch_locked_artifact(name, artifact, cache / "archives", offline=False)
        destination = cache / "extracted" / name
        if destination.exists() and not _extracted_tree_is_complete(name, destination):
            shutil.rmtree(destination, ignore_errors=True)
        if not destination.exists():
            safe_extract_zip(
                archive,
                destination,
                strip_prefix=str(artifact["strip_prefix"]),
                include=artifact.get("include"),
            )
        extracted[name] = destination
    return extracted


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def _postgres_sources(postgres_root: Path) -> dict[str, Path]:
    postgres_root = _require_real_directory(postgres_root, label="PostgreSQL source root")
    sources: dict[str, Path] = {}

    for filename, (expected_bytes, expected_sha256) in sorted(POSTGRES_BIN_PINS.items()):
        path = postgres_root / "bin" / filename
        _validate_pinned_file(
            path,
            expected_bytes=expected_bytes,
            expected_sha256=expected_sha256,
            label=f"pinned PostgreSQL {filename}",
        )
        sources[f"bin/{filename}"] = path
    for filename, (expected_bytes, expected_sha256) in sorted(POSTGRES_BIN_DLL_PINS.items()):
        path = postgres_root / "bin" / filename
        _validate_pinned_file(
            path,
            expected_bytes=expected_bytes,
            expected_sha256=expected_sha256,
            label=f"pinned PostgreSQL runtime dependency {filename}",
        )
        sources[f"bin/{filename}"] = path
    for filename, (expected_bytes, expected_sha256) in sorted(POSTGRES_LIB_PINS.items()):
        path = postgres_root / "lib" / filename
        _validate_pinned_file(
            path,
            expected_bytes=expected_bytes,
            expected_sha256=expected_sha256,
            label=f"pinned PostgreSQL extension {filename}",
        )
        sources[f"lib/{filename}"] = path

    for filename in POSTGRES_SHARE_TOP_FILES:
        path = _require_regular_file(
            postgres_root / "share" / filename, label=f"PostgreSQL share/{filename}"
        )
        sources[f"share/{filename}"] = path
    for filename in POSTGRES_SHARE_EXTENSION_FILES:
        path = _require_regular_file(
            postgres_root / "share" / "extension" / filename,
            label=f"PostgreSQL share/extension/{filename}",
        )
        sources[f"share/extension/{filename}"] = path
    for subdir in POSTGRES_SHARE_DATA_DIRS:
        sources.update(
            _collect_data_tree(postgres_root / "share" / subdir, dest_prefix=f"share/{subdir}")
        )
    for filename in POSTGRES_LICENSE_FILES:
        path = _require_regular_file(
            postgres_root / filename, label=f"PostgreSQL license file {filename}"
        )
        sources[f"licenses/postgresql/{filename}"] = path
    return sources


def _tsduck_sources(tsduck_root: Path) -> dict[str, Path]:
    tsduck_root = _require_real_directory(tsduck_root, label="TSDuck source root")
    sources: dict[str, Path] = {}
    for filename, (expected_bytes, expected_sha256) in sorted(TSDUCK_BIN_PINS.items()):
        path = tsduck_root / "bin" / filename
        _validate_pinned_file(
            path,
            expected_bytes=expected_bytes,
            expected_sha256=expected_sha256,
            label=f"pinned TSDuck {filename}",
        )
        sources[f"tsduck/bin/{filename}"] = path
    for filename, (expected_bytes, expected_sha256) in sorted(TSDUCK_DATA_PINS.items()):
        path = tsduck_root / "bin" / filename
        _validate_pinned_file(
            path,
            expected_bytes=expected_bytes,
            expected_sha256=expected_sha256,
            label=f"pinned TSDuck data file {filename}",
        )
        sources[f"tsduck/bin/{filename}"] = path
    for filename in TSDUCK_LICENSE_FILES:
        path = _require_regular_file(
            tsduck_root / filename, label=f"TSDuck license file {filename}"
        )
        sources[f"licenses/tsduck/{filename}"] = path
    return sources


_ACQUIRE_VALIDATORS: Final[dict[str, Callable[[Path], dict[str, Path]]]] = {
    "postgres": _postgres_sources,
    "tsduck": _tsduck_sources,
}


def _extracted_tree_is_complete(name: str, destination: Path) -> bool:
    """Re-verify a pre-existing extraction against the SAME pinned bin/lib/
    share file set `build_server_pack()` itself requires, rather than
    trusting bare directory existence.

    A self-hosted runner's `--cache` persists across runs (a hosted runner
    is always fresh); a previous run's extraction into `cache/extracted/
    <name>` interrupted partway through (a crash, a cancelled run, a disk
    hiccup) leaves a directory that EXISTS but is missing files --
    candidate run 32845198987 failed identically in BOTH attempts with
    "pinned PostgreSQL initdb.exe is missing" from exactly this path,
    surfaced only much later, deep inside the live PostgreSQL bootstrap
    proof, rather than here where the incompleteness actually originates.
    """
    try:
        _ACQUIRE_VALIDATORS[name](destination)
    except ServerPackBuildError:
        return False
    return True


def _require_zero_gpl_and_full_license_provenance(sources: dict[str, Path]) -> None:
    """Refuse the build if any packed path has no confirmed license, or a
    confirmed license that is GPL-family (task's zero GPL/AGPL tolerance).
    Runs on every path this build is ABOUT to pack, so a future addition to
    the source-selection functions above that forgets to update
    ``civiccast.native.runtime_licenses`` fails the build loud instead of
    shipping an unreviewed file silently."""

    unresolved: list[str] = []
    gpl_flagged: list[tuple[str, str]] = []
    for relative_path in sorted(sources):
        if relative_path.startswith(("notices/",)):
            continue  # this builder's own generated NOTICE, not upstream bytes
        license_id = classify_server_pack_file(relative_path)
        if license_id is None:
            unresolved.append(relative_path)
        elif is_gpl_license(license_id):
            gpl_flagged.append((relative_path, license_id))
    if gpl_flagged:
        raise ServerPackBuildError(
            "native-server-binaries pack refuses GPL/AGPL-family entries: "
            + ", ".join(f"{path} ({license_id})" for path, license_id in gpl_flagged)
        )
    if unresolved:
        raise ServerPackBuildError(
            "native-server-binaries pack has unconfirmed license provenance for: "
            + ", ".join(unresolved[:10])
            + (f" (+{len(unresolved) - 10} more)" if len(unresolved) > 10 else "")
        )


def _free_loopback_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _grant_scratch_tree_to_current_user(root: Path) -> None:
    """Give the current user's OWN SID explicit rights on the proof's
    scratch tree (Windows only; no-op elsewhere).

    LESSON (candidate runs 31143881561 and 31154873108, diagnosed live on
    the runner via PR #368's three-leg probe): PostgreSQL's Windows
    binaries re-exec themselves under a RESTRICTED token that marks the
    Administrators group deny-only (``get_restricted_token``). On GitHub's
    hosted runners the scratch drive's ACLs grant access through
    Administrators alone, so the re-exec'd ``initdb`` child cannot read
    the DLLs beside its own executable and its loader dies with
    0xC0000135 (STATUS_DLL_NOT_FOUND) and NO output -- the parent
    silently propagates that status, which is exactly the empty-output
    failure both candidate runs showed. ``initdb --version`` exits before
    the re-exec, which is why simple load probes pass on the same image.

    Granting the user's own SID mirrors what an installed station already
    has (pack payloads land under ProgramData with user-readable ACLs --
    the 2026-07-30 Sandbox run's initdb got far past loading), so this
    keeps the proof faithful to station reality rather than weakening it:
    probe leg A (no grant) reproduced 0xC0000135 on the runner; leg C
    (this exact grant) ran initdb to its first real bootstrap step.
    """

    if os.name != "nt":
        return
    whoami = subprocess.run(["whoami"], capture_output=True, text=True, timeout=60, check=False)
    account = whoami.stdout.strip()
    if whoami.returncode != 0 or not account:
        raise ServerPackBuildError(
            "bootstrap proof could not resolve the current user for the "
            f"scratch-tree access grant (whoami exit {whoami.returncode})"
        )
    grant = subprocess.run(
        ["icacls", str(root), "/grant:r", f"{account}:(OI)(CI)F", "/T"],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if grant.returncode != 0:
        raise ServerPackBuildError(
            "bootstrap proof could not grant the current user access to its "
            f"scratch tree (icacls exit {grant.returncode}): "
            f"{grant.stdout.strip()} {grant.stderr.strip()}"
        )


def prove_postgres_bootstrap(
    postgres_root: Path,
    *,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> None:
    """Live build-time proof that the EXACT ``lib``/``bin``/``share``
    selection this builder packs can bootstrap and serve a real cluster.

    Sandbox matrix row 1, run 2 (2026-07-30): the first live ``initdb``
    against a packed tree died on a ``lib/`` module the selection had
    trimmed (``$libdir/utf8_and_win``) -- a class of omission no
    hash/path-set validation can catch, because every file the selection
    DOES name was present and correct. The only authority on completeness
    is PostgreSQL itself, so every real pack build now materializes the
    selected files into a scratch tree and runs, in order: ``initdb``
    (the SAME argv shape provisioning uses -- ``initdb_argv`` imported
    from ``civiccast.native.provision.seams``, never a lookalike),
    ``pg_ctl start`` on a free loopback port, a ``psql`` probe that
    exercises the pinned extension (``CREATE EXTENSION btree_gist``) and
    the snowball dictionary path (``to_tsvector``), then ``pg_ctl stop``.
    Any nonzero step fails the BUILD with the step's full output -- never
    a station install. ``run`` is injectable for the unit suite (hard
    rule: no real postgres execution in unit tests; the live execution
    happens on every real CLI pack build).
    """

    sources = secured_postgres_sources(
        _postgres_sources(postgres_root), postgres_root.parent / "openssl-security-cache"
    )
    with tempfile.TemporaryDirectory(
        prefix="civiccast-pack-bootstrap-proof-", ignore_cleanup_errors=True
    ) as temporary:
        root = Path(temporary)
        pgroot = root / "pg"
        for key, src in sources.items():
            if key.split("/", 1)[0] not in ("bin", "lib", "share"):
                continue
            dest = pgroot.joinpath(*PurePosixPath(key).parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        # Without this, initdb's restricted-token re-exec cannot read the
        # staged tree on GitHub-hosted runners and dies 0xC0000135 before
        # its first print -- see _grant_scratch_tree_to_current_user.
        _grant_scratch_tree_to_current_user(root)
        pwfile = root / "bootstrap-proof-pwfile.txt"
        pwfile.write_text("civiccast-bootstrap-proof\n", encoding="ascii")
        pgdata = root / "pgdata"
        logfile = root / "postgres-proof.log"
        port = _free_loopback_port()
        env = {**os.environ, "PGPASSWORD": "civiccast-bootstrap-proof"}

        step_index = 0

        def check(step: str, argv: list[str]) -> None:
            # Step output goes to FILES, never subprocess pipes: ``pg_ctl
            # start`` leaves a detached ``postgres.exe`` holding inherited
            # copies of the parent's stdout/stderr handles, and with PIPE
            # capture ``subprocess.run`` blocks on pipe EOF until the server
            # exits -- a deadlock observed live on this proof's first
            # passing initdb (2026-07-30). File handles are inherited too,
            # but nothing waits on their closure.
            nonlocal step_index
            step_index += 1
            out_path = root / f"proof-step-{step_index:02d}-output.txt"
            with out_path.open("w", encoding="utf-8") as sink:
                result = run(argv, stdout=sink, stderr=subprocess.STDOUT, text=True, env=env)
            if result.returncode != 0:
                captured = out_path.read_text(encoding="utf-8", errors="replace")
                log_tail = ""
                if logfile.exists():
                    log_tail = "\n--- server log tail ---\n" + "\n".join(
                        logfile.read_text(encoding="utf-8", errors="replace").splitlines()[-25:]
                    )
                raise ServerPackBuildError(
                    f"pack bootstrap proof failed at {step} (exit {result.returncode}). "
                    "The packed PostgreSQL selection cannot bootstrap a live cluster -- "
                    "most likely a bin/lib/share file the pins do not carry. Fix the "
                    "selection (see POSTGRES_LIB_PINS' lesson comment); do NOT ship "
                    f"this pack.\n--- output ---\n{captured}{log_tail}"
                )

        bin_dir = pgroot / "bin"
        check(
            "initdb",
            initdb_argv(
                initdb_path=str(bin_dir / "initdb.exe"),
                data_dir=str(pgdata),
                username="civiccast",
                pwfile=str(pwfile),
            ),
        )
        started = False
        try:
            check(
                "pg_ctl start",
                [
                    str(bin_dir / "pg_ctl.exe"),
                    "--pgdata",
                    str(pgdata),
                    "--log",
                    str(logfile),
                    "--wait",
                    "--timeout",
                    "60",
                    "--options",
                    f"-p {port} -c listen_addresses=127.0.0.1",
                    "start",
                ],
            )
            started = True
            check(
                "psql probe",
                [
                    str(bin_dir / "psql.exe"),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--username",
                    "civiccast",
                    "--dbname",
                    "postgres",
                    "--set",
                    "ON_ERROR_STOP=1",
                    "--command",
                    "CREATE EXTENSION btree_gist;",
                    "--command",
                    "SELECT to_tsvector('english', 'pack bootstrap proof');",
                    "--command",
                    "DO $$ BEGIN IF xpath_exists('/agenda/item[@id=\"1\"]', "
                    "XMLPARSE(DOCUMENT '<agenda><item id=\"1\">ready</item></agenda>')) "
                    "IS NOT TRUE THEN RAISE EXCEPTION 'pack XML parser failed'; END IF; END $$;",
                ],
            )
        finally:
            if started:
                with (root / "proof-step-99-stop-output.txt").open("w", encoding="utf-8") as sink:
                    run(
                        [
                            str(bin_dir / "pg_ctl.exe"),
                            "--pgdata",
                            str(pgdata),
                            "--mode",
                            "fast",
                            "--wait",
                            "stop",
                        ],
                        stdout=sink,
                        stderr=subprocess.STDOUT,
                        text=True,
                        env=env,
                    )


def build_server_pack(
    *,
    output: Path,
    postgres_root: Path,
    tsduck_root: Path,
    signing_private_key: Ed25519PrivateKey,
    signing_key_id: str,
    product_version: str,
    source_sha: str,
    compatible_core: str | None = None,
) -> dict[str, object]:
    """Validate the pinned PostgreSQL/TSDuck inputs and build the
    signed ``native-server-binaries`` pack.

    The returned report's ``payload_tree_sha256`` (from
    ``civiccast.installer.native_packs.payload_tree_sha256``, applied to the
    SAME manifest ``files`` entries ``build_native_pack`` already computes
    and signs -- not a second, independent hashing pass) lets two machines
    that each built this pack from the same commit compare their PAYLOAD
    bytes decisively, even though their ``pack_sha256``/signing key id
    necessarily differ (each machine signs with its own local development
    key). Equal ``payload_tree_sha256`` across machines is the actual
    reproducible-build proof; equal ``payload_bytes``/``file_count`` alone
    is not (same size and count can hide a rename or a same-size content
    swap)."""

    if not (
        isinstance(source_sha, str)
        and len(source_sha) == 40
        and all(character in "0123456789abcdef" for character in source_sha)
    ):
        raise ServerPackBuildError("source SHA must be exactly 40 lowercase hexadecimal characters")

    sources = {
        **secured_postgres_sources(
            _postgres_sources(postgres_root), postgres_root.parent / "openssl-security-cache"
        ),
        **_tsduck_sources(tsduck_root),
    }

    notice = (
        "CivicCast native server-binaries pack\n"
        f"PostgreSQL {POSTGRES_VERSION} (bin/initdb.exe, bin/postgres.exe, "
        "bin/pg_ctl.exe, bin/pg_dump.exe, bin/pg_dumpall.exe, bin/pg_restore.exe, bin/psql.exe, "
        "the runtime DLLs they import, the btree_gist extension, and required "
        "share/ bootstrap + timezone data)\n"
        f"TSDuck {TSDUCK_VERSION} subset (tsduck/bin/tsp.exe, tscore.dll, "
        "tsduck.dll, the analyze/continuity/pcradjust/until plugins, and the "
        "tsduck/tscore .names/.xml configuration data files tsp resolves "
        "relative to its own directory)\n"
        "See licenses/ for upstream license texts and "
        "civiccast.native.runtime_licenses.SERVER_PACK_BASENAME_LICENSE for "
        "the per-file provenance table.\n"
    )
    with tempfile.TemporaryDirectory(prefix="civiccast-server-pack-") as temporary:
        notice_path = Path(temporary) / "NOTICE.txt"
        notice_path.write_text(notice, encoding="utf-8", newline="\n")
        sources["notices/server-binaries.txt"] = notice_path

        _require_zero_gpl_and_full_license_provenance(sources)

        result = build_native_pack(
            output=output,
            component=SERVER_BINARIES_COMPONENT,
            product_version=product_version,
            compatible_core=compatible_core or product_version,
            sources=sources,
            signing_private_key=signing_private_key,
            signing_key_id=signing_key_id,
            metadata={
                "postgres_version": POSTGRES_VERSION,
                "tsduck_version": TSDUCK_VERSION,
                "source_sha": source_sha,
            },
        )
    return {
        "component": result.component,
        "file_count": result.file_count,
        "output": str(result.path),
        "pack_bytes": result.path.stat().st_size,
        "pack_sha256": result.sha256,
        "payload_bytes": result.total_bytes,
        "payload_tree_sha256": result.payload_tree_sha256,
        "product_version": result.product_version,
        "signing_key_id": result.signing_key_id,
        "source_sha": source_sha,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--acquire",
        action="store_true",
        help=(
            "download + verify + extract postgres/tsduck from the reviewed "
            "lock into --cache before building (mutually exclusive with the "
            "--*-root flags)"
        ),
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path(tempfile.gettempdir()) / "civiccast-native-server-pack-cache",
        help="scratch directory OUTSIDE the repo for --acquire's downloads/extraction",
    )
    parser.add_argument("--lock", type=Path, default=LOCK_PATH)
    parser.add_argument("--postgres-root", type=Path)
    parser.add_argument("--tsduck-root", type=Path)
    parser.add_argument("--signing-private-key", required=True, type=Path)
    parser.add_argument("--signing-key-id", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--product-version", default=__version__)
    parser.add_argument("--compatible-core", default=None)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--allow-development-key",
        action="store_true",
        help="explicitly allow a development-only trust root for non-release proof",
    )
    parser.add_argument(
        "--skip-bootstrap-proof",
        action="store_true",
        help=(
            "skip the live initdb/server bootstrap proof of the packed "
            "PostgreSQL selection (emergency/debug ONLY -- the proof exists "
            "because a trimmed selection shipped a pack whose first live "
            "initdb died; see POSTGRES_LIB_PINS)"
        ),
    )
    args = parser.parse_args()

    try:
        require_allowed_signing_key(
            args.signing_key_id, allow_development_key=args.allow_development_key
        )
        key = load_ed25519_private_key(args.signing_private_key)

        if args.acquire:
            if args.postgres_root or args.tsduck_root:
                raise ServerPackBuildError("--acquire is mutually exclusive with --*-root flags")
            roots = acquire_server_pack_sources(args.cache, lock_path=args.lock)
            postgres_root, tsduck_root = (
                roots["postgres"],
                roots["tsduck"],
            )
        else:
            missing = [
                flag
                for flag, value in (
                    ("--postgres-root", args.postgres_root),
                    ("--tsduck-root", args.tsduck_root),
                )
                if value is None
            ]
            if missing:
                raise ServerPackBuildError(
                    f"missing required flags (or pass --acquire instead): {', '.join(missing)}"
                )
            postgres_root, tsduck_root = (
                args.postgres_root,
                args.tsduck_root,
            )

        if args.skip_bootstrap_proof:
            print(
                "build_native_server_pack: WARNING -- bootstrap proof SKIPPED "
                "(--skip-bootstrap-proof); this pack's PostgreSQL selection is "
                "unproven against a live initdb",
                file=sys.stderr,
            )
        else:
            print("build_native_server_pack: running live bootstrap proof (initdb + server)...")
            prove_postgres_bootstrap(postgres_root)
            print("build_native_server_pack: bootstrap proof PASSED")

        report = build_server_pack(
            output=args.output.resolve(),
            postgres_root=postgres_root,
            tsduck_root=tsduck_root,
            signing_private_key=key,
            signing_key_id=args.signing_key_id,
            product_version=args.product_version,
            source_sha=args.source_sha,
            compatible_core=args.compatible_core,
        )
    except ServerPackBuildError as exc:
        print(f"build_native_server_pack: {exc}", file=sys.stderr)
        return 1

    rendered = json.dumps(report, indent=2) + "\n"
    if args.report is not None:
        report_path = args.report.resolve()
        if report_path.exists():
            raise FileExistsError(f"server pack report already exists: {report_path}")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(rendered, encoding="utf-8", newline="\n")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
