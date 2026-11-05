# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Red-first tests for the per-file license classifier (`runtime_licenses`).

This module is deliberately independent of `runtime_manifest.py` (a
different worker owns that file concurrently) -- these tests exercise
`civiccast.native.runtime_licenses` in isolation, against the evidence
recorded in `.agent-runs/native-windows/ws5-packaging-closure/evidence/
license-provenance.md`:

- Direct `gst_plugin_get_license()` queries against the actual DLLs in the
  built tree (see the evidence memo's "Plugin sweep" section) for every one
  of the 34 GStreamer plugin DLLs the closure ships.
- Direct `avutil_license()` / `avcodec_license()` / `avformat_license()` /
  `avfilter_license()` / `swscale_license()` / `swresample_license()` calls
  against the shipped FFmpeg DLLs.
- The wheel-cache `dist-info/METADATA` `License:` fields for all seven
  installed GStreamer distributions plus the two EXCLUDED
  `gstreamer_plugins_gpl*` distributions (confirmed absent from
  `requirements-native-runtime.txt`).

AC7 (`spec-packaging-closure`): an unresolved file must come back `None`,
never a guessed default. `is_gpl_license` is the explicit GPL-detection
predicate the investigation's core question depends on.
"""

from __future__ import annotations

from civiccast.native.runtime_licenses import (
    DUAL_LICENSE_ELECTIONS,
    GENERATED_ARTIFACT_LICENSE,
    UNRESOLVED_BASENAMES,
    classify_shipped_file,
    is_gpl_license,
)

# ---------------------------------------------------------------------------
# classify_shipped_file -- known files resolve to their evidence-backed license
# ---------------------------------------------------------------------------


def test_ffmpeg_libs_resolve_to_lgpl_from_direct_runtime_probe() -> None:
    # avutil_license()/avcodec_license()/avformat_license()/avfilter_license()
    # all returned "LGPL version 2.1 or later" when queried directly against
    # the shipped DLLs (evidence memo, FFmpeg section).
    # RC1's minimal same-ABI build also links the Microsoft CRT statically.
    expected = "LGPL-2.1-or-later AND LicenseRef-Microsoft-VCRedist"
    for name in (
        "avutil-59.dll",
        "avcodec-61.dll",
        "avformat-61.dll",
        "avfilter-10.dll",
        "swresample-5.dll",
        "swscale-8.dll",
    ):
        assert classify_shipped_file(f"bin/{name}") == expected


def test_gstreamer_security_overlay_pins_match_classifier_contract() -> None:
    import json
    from pathlib import Path

    lock_path = Path(__file__).resolve().parents[2] / "native-windows-security-libraries.lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))

    exercised = set()
    for entry in lock["files"].values():
        if "gstreamer" not in entry:
            continue
        gstreamer = entry["gstreamer"]
        exercised.add(gstreamer)
        assert classify_shipped_file(gstreamer) == entry["license"], gstreamer

    assert "bin/libexpat.dll" in exercised
    assert "licenses/security-libs/expat-LICENSE.txt" in exercised


def test_gstreamer_plugin_dlls_resolve_per_direct_plugin_sweep() -> None:
    # gst_plugin_get_license() queried against every plugin DLL loaded from
    # the built tree (evidence memo, "34-plugin sweep" table).
    assert classify_shipped_file("lib/gstreamer-1.0/gstcoreelements.dll") == "LGPL-2.1-or-later"
    assert classify_shipped_file("lib/gstreamer-1.0/gstlibav.dll") == "LGPL-2.1-or-later"
    assert classify_shipped_file("lib/gstreamer-1.0/gstpng.dll") == "LGPL-2.1-or-later"
    assert classify_shipped_file("lib/gstreamer-1.0/gstaudiofx.dll") == "LGPL-2.1-or-later"
    assert classify_shipped_file("lib/gstreamer-1.0/gstrsaudiofx.dll") == "MPL-2.0"
    assert classify_shipped_file("lib/gstreamer-1.0/gstopenh264.dll") == "BSD-2-Clause"
    assert classify_shipped_file("lib/gstreamer-1.0/gstrsclosedcaption.dll") == "MPL-2.0"


def test_openh264_and_voaacenc_support_libs_match_their_own_upstream_license() -> None:
    # Cisco OpenH264 codec lib: BSD-2-Clause (matches the gstopenh264 plugin's
    # own "BSD" self-report). Patent-grant posture is a separate, unresolved
    # OWNER-acceptance question (spec D3) -- not an SPDX license question --
    # and is called out explicitly in the evidence memo, not silently folded
    # into this classification.
    assert classify_shipped_file("bin/openh264-7.dll") == "BSD-2-Clause"
    # VisualOn AAC encoder: Apache-2.0 (matches "Apache-2.0" in the
    # gstreamer_plugins_restricted wheel's aggregate METADATA).
    assert classify_shipped_file("bin/vo-aacenc-0.dll") == "Apache-2.0"


def test_gstfft_support_library_records_both_compiled_upstream_licenses() -> None:
    assert classify_shipped_file("bin/gstfft-1.0-0.dll") == ("LGPL-2.0-or-later AND BSD-3-Clause")


def test_pygobject_python_files_resolve_via_directory_prefix() -> None:
    # Every file under python/gi/ ships from the gstreamer_python wheel
    # (LGPL-2.1-or-later in its own METADATA), regardless of the individual
    # file's generic basename (module.py, types.py, __init__.py, ...).
    assert classify_shipped_file("python/gi/__init__.py") == "LGPL-2.1-or-later"
    assert classify_shipped_file("python/gi/overrides/__init__.py") == "LGPL-2.1-or-later"
    assert classify_shipped_file("python/gi/repository/__init__.py") == "LGPL-2.1-or-later"
    assert classify_shipped_file("python/gi/_gi.cp312-win_amd64.pyd") == "LGPL-2.1-or-later"


def test_typelibs_resolve_to_their_originating_projects_license() -> None:
    assert classify_shipped_file("lib/girepository-1.0/Gst-1.0.typelib") == "LGPL-2.1-or-later"
    assert classify_shipped_file("lib/girepository-1.0/GLib-2.0.typelib") == "LGPL-2.1-or-later"
    assert classify_shipped_file("lib/girepository-1.0/HarfBuzz-0.0.typelib") == "MIT"


def test_generated_bom_artifacts_resolve_to_the_repo_license_not_upstream() -> None:
    # These files are written by build_native_runtime_closure.py itself --
    # original CivicCast text, not redistributed upstream binaries -- so
    # they carry the repo's own Apache-2.0, not a DISTRIBUTION_LICENSE entry.
    assert classify_shipped_file("LICENSE-BOM.md") == GENERATED_ARTIFACT_LICENSE
    assert classify_shipped_file("runtime-manifest.json") == GENERATED_ARTIFACT_LICENSE
    assert classify_shipped_file("SHA256SUMS") == GENERATED_ARTIFACT_LICENSE
    assert classify_shipped_file("licenses/README.md") == GENERATED_ARTIFACT_LICENSE
    assert classify_shipped_file("licenses/gstreamer_libs.txt") == GENERATED_ARTIFACT_LICENSE


# ---------------------------------------------------------------------------
# classify_shipped_file -- the mislabeling finding (this is the "inconvenient
# result": the vcredist files are NOT LGPL, contradicting the DISTRIBUTION_
# LICENSE["gstreamer_ext_runtime"] = "LGPL-2.1-or-later" entry in
# runtime_manifest.py, which is based on a factually wrong comment claiming
# gstreamer_ext_runtime "carries gstlibav.dll" -- it does not; verified by
# direct wheel-cache inspection, see the evidence memo's "gstreamer_ext_
# runtime mislabel" section).
# ---------------------------------------------------------------------------


def test_msvc_redistributable_files_are_proprietary_not_lgpl() -> None:
    for path in (
        "bin/msvcp140.dll",
        "bin/vcruntime140.dll",
        "bin/vcruntime140_1.dll",
    ):
        license_ = classify_shipped_file(path)
        assert license_ == "LicenseRef-Microsoft-VCRedist", path
        assert license_ != "LGPL-2.1-or-later", path
        assert not is_gpl_license(license_)


def test_gpl_detection_handles_case_and_pypi_classifier_text() -> None:
    assert is_gpl_license("gpl-3.0-only")
    assert is_gpl_license("GNU General Public License v3 (GPLv3)")
    assert not is_gpl_license("GNU Lesser General Public License v3 (LGPLv3)")


# ---------------------------------------------------------------------------
# classify_shipped_file -- AC7's hard requirement: never guess
# ---------------------------------------------------------------------------


def test_unknown_path_returns_none_rather_than_a_default() -> None:
    assert classify_shipped_file("bin/some-unrecognized-support-lib.dll") is None
    assert classify_shipped_file("lib/gstreamer-1.0/gstnotarealplugin.dll") is None


def test_gpl_only_plugins_that_are_never_shipped_stay_unresolved_not_guessed() -> None:
    # gstx264.dll / gsta52dec.dll / gstdtsdec.dll / gstdvdread.dll /
    # gstresindvd.dll / gstx265.dll ship ONLY from the two excluded
    # gstreamer_plugins_gpl* wheels (confirmed absent from
    # requirements-native-runtime.txt). This module was never told a license
    # for them -- if one of these paths ever showed up in a real build, the
    # classifier must refuse to resolve it (None), not silently mint a
    # license for a file this investigation never examined.
    for basename in (
        "gstx264.dll",
        "gstx265.dll",
        "gsta52dec.dll",
        "gstdtsdec.dll",
        "gstdvdread.dll",
        "gstresindvd.dll",
    ):
        assert classify_shipped_file(f"lib/gstreamer-1.0/{basename}") is None


def test_fontconfig_carries_its_whole_cumulative_notice_not_just_the_main_grant() -> None:
    """fontconfig 2.16.1 is a cumulative multi-notice component.

    This test previously asserted `== "HPND-sell-variant"` and passed, because
    that IS fontconfig's primary grant. It was still wrong: reading the exact
    upstream release commit's COPYING
    (fdfc3445d1cc9c1c7e587fb2a1287871de16faf9) shows the main HPND-sell-variant
    grant is followed by four further notice families covering sources and data
    that are compiled into the shipped DLL -- Unicode terms for CaseFolding,
    HarfBuzz-derived terms for the atomic/mutex headers, MIT for fcfoundry, and
    public-domain dedications for fcmd5/ftglue. Naming only the first one is
    incomplete provenance (Codex CC-WS5-PKG-004 round 2).

    Asserting the exact expression, not just `in`, so dropping a component is a
    failure rather than something a substring check would wave through.

    Not a dual-license election: nothing here is an "A OR B" choice, so
    fontconfig must NOT appear in DUAL_LICENSE_ELECTIONS.

    It cannot simply be pruned instead: cairo, pangocairo and pangoft2 all
    import it in the real built tree.
    """
    expected = (
        "HPND-sell-variant AND MIT AND MIT-Modern-Variant AND Unicode-TOU "
        "AND LicenseRef-Fontconfig-2.16.1"
    )
    assert classify_shipped_file("bin/fontconfig-1.dll") == expected
    # The typelib carries the same project's terms -- the two must never drift.
    assert classify_shipped_file("lib/girepository-1.0/fontconfig-2.0.typelib") == expected
    assert not is_gpl_license(expected)
    assert "fontconfig-1.dll" not in DUAL_LICENSE_ELECTIONS


def test_freetype_is_documented_as_a_dual_license_election_not_a_discovery() -> None:
    """CC-WS5-PKG-005: FreeType is dual-licensed upstream "FTL OR GPL-2.0" --
    the redistributor elects. FreeType was previously recorded as though FTL
    were simply its license, unlike the other dual-licence elections
    (librtmp, cairo) which ARE recorded as elections. Both the DLL and
    its typelib must be documented in `DUAL_LICENSE_ELECTIONS`, matching the
    pattern used for every other genuine dual-license election in this table.
    """
    assert "freetype-6.dll" in DUAL_LICENSE_ELECTIONS
    assert "freetype2-2.0.typelib" in DUAL_LICENSE_ELECTIONS
    assert classify_shipped_file("bin/freetype-6.dll") == "FTL"
    assert classify_shipped_file("lib/girepository-1.0/freetype2-2.0.typelib") == "FTL"
    assert not is_gpl_license("FTL")


def test_dual_license_elections_registry_covers_every_known_election() -> None:
    """The dual-license elections that survive the WP-6 prune (librtmp,
    cairo/cairo-gobject, FreeType) must be present in the registry -- each is a
    genuine redistributor election disclosed as such, not a special case with
    its own separate mechanism. The two D-Bus typelib elections were removed
    with the pruned typelibs (see test_pruned_dbus_typelibs_*).
    """
    for basename in (
        "rtmp-1.dll",
        "cairo-2.dll",
        "cairo-gobject-2.dll",
        "freetype-6.dll",
        "freetype2-2.0.typelib",
    ):
        assert basename in DUAL_LICENSE_ELECTIONS


def test_pruned_dbus_typelibs_are_fully_removed_from_the_license_machinery() -> None:
    """WP-6 Part 0 (owner-approved 2026-07-24): DBus-1.0.typelib and
    DBusGLib-1.0.typelib are pruned from the shipped closure, so every trace of
    them in the license machinery goes with them -- they must NOT resolve to a
    license (they are no longer shipped, so classifying them would be a claim
    about a file that is not in the tree) and must NOT appear in the
    dual-license election registry. AFL-2.1 was elected ONLY by these two, so
    its bundled text is removed too (see the license_texts test).
    """
    from civiccast.native.runtime_licenses import TYPELIB_LICENSE

    for basename in ("DBus-1.0.typelib", "DBusGLib-1.0.typelib"):
        assert basename not in TYPELIB_LICENSE
        assert basename not in DUAL_LICENSE_ELECTIONS
        assert classify_shipped_file(f"lib/girepository-1.0/{basename}") is None
    # AFL-2.1 was elected ONLY by the two pruned typelibs -- no other shipped
    # file resolves to it, so it must be gone from every license table.
    assert "AFL-2.1" not in TYPELIB_LICENSE.values()


def test_unresolved_mechanism_still_exists_and_is_currently_empty() -> None:
    """AC7 depends on there being a way to say "provenance unknown" that halts
    the build. The set being empty is a statement that there are currently no
    unknowns -- not that the concept was quietly dropped once it became
    inconvenient. If this ever becomes non-empty, the build must halt."""
    assert not UNRESOLVED_BASENAMES
    for basename in UNRESOLVED_BASENAMES:  # pragma: no cover - empty today
        assert classify_shipped_file(f"bin/{basename}") is None


# ---------------------------------------------------------------------------
# is_gpl_license -- the explicit GPL-detection predicate
# ---------------------------------------------------------------------------


def test_is_gpl_license_catches_gpl_2_and_gpl_3_variants() -> None:
    assert is_gpl_license("GPL-2.0-or-later")
    assert is_gpl_license("GPL-2.0-only")
    assert is_gpl_license("GPL-3.0-only")
    assert is_gpl_license("GPL-3.0-or-later")
    assert is_gpl_license("GPL")


def test_is_gpl_license_does_not_false_positive_on_lgpl_or_agpl() -> None:
    # The bug this predicate must never have: naive substring matching
    # ("GPL" in s) flags "LGPL-2.1-or-later" as GPL because "GPL" is a
    # substring of "LGPL". Token-based matching must not make that mistake.
    assert not is_gpl_license("LGPL-2.1-or-later")
    assert not is_gpl_license("LGPL-2.0-or-later")
    assert not is_gpl_license("LGPL-3.0-only")
    assert not is_gpl_license("AGPL-3.0-or-later")


def test_is_gpl_license_catches_gpl_inside_a_dual_license_or_expression() -> None:
    # librtmp is genuinely dual-licensed "LGPL-2.1-or-later OR
    # GPL-2.0-or-later" upstream. The predicate must flag the expression as
    # containing a GPL option even when it is not the sole term -- callers
    # that have elected the LGPL branch pass the ELECTED single license
    # string to this predicate, never the raw dual expression, but the
    # predicate itself must not be fooled by an OR-combined string either.
    assert is_gpl_license("LGPL-2.1-or-later OR GPL-2.0-or-later")


def test_is_gpl_license_false_for_non_gpl_families() -> None:
    for license_ in ("MIT", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "MPL-2.0", "Zlib"):
        assert not is_gpl_license(license_)
