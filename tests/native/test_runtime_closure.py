# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Red-first tests for the native Windows runtime packaging closure.

Covers `spec-packaging-closure` D2 (closure = static PE walk + resources),
D3 (no GPL in the shipped runtime), and AC3 (GPL negative control).

The closure core is deliberately pure: the PE-import walk takes `imports_of`
and `resolve` as injected callables so the graph logic is provable without any
real PE file, and the same functions run against `pefile` in the build script.
"""

from __future__ import annotations

import pytest

from civiccast.native.runtime_closure import (
    ABSENCE_TOLERANT_FACTORIES,
    AUTHORIZED_RUNTIME_DISTRIBUTIONS,
    AUTHORIZED_STAGED_DISTRIBUTIONS,
    CONDITIONAL_FACTORIES,
    EXCLUDED_GPL_FACTORIES,
    FACTORY_PLUGIN,
    GPL_DISTRIBUTIONS,
    LINUX_ONLY_FACTORIES,
    REQUIRED_FACTORIES,
    STAGED_OPTIONAL_FACTORIES,
    GplPolicyError,
    MissingPluginError,
    UnauthorizedDistributionError,
    UnknownProvenanceError,
    assert_authorized_distributions,
    assert_no_gpl_distributions,
    resolve_pe_closure,
    select_plugin_seeds,
)

# --------------------------------------------------------------------------
# Element policy
# --------------------------------------------------------------------------


def test_x264_and_x265_are_excluded_not_required() -> None:
    """The no-GPL BOM decision: x264enc/x265enc never ship.

    They appear in the original spike's required list because that list was
    captured from the WSL runtime before the no-GPL decision. Shipping either
    would breach ADR-0021.
    """
    assert set(EXCLUDED_GPL_FACTORIES) == {"x264enc", "x265enc"}
    assert not (REQUIRED_FACTORIES & EXCLUDED_GPL_FACTORIES)
    assert not (CONDITIONAL_FACTORIES & EXCLUDED_GPL_FACTORIES)


def test_hevc_hardware_encoder_is_required() -> None:
    """Item 3 made HEVC a real native path (mfh265enc + NV12 pinning), so the
    Media Foundation plugin must be in the shipped closure even though the
    factory only registers on machines with an HEVC MFT."""
    assert "mfh265enc" in REQUIRED_FACTORIES
    assert "mfh264enc" in REQUIRED_FACTORIES
    assert FACTORY_PLUGIN["mfh265enc"] == FACTORY_PLUGIN["mfh264enc"] == "gstmediafoundation.dll"


def test_every_policy_factory_has_a_plugin_mapping() -> None:
    """The build must never depend on live factory probing: hardware-gated
    factories (MF, NVENC) do not register on a build box without matching GPU
    hardware, which would make the closure machine-dependent and break AC1."""
    covered = (
        REQUIRED_FACTORIES
        | CONDITIONAL_FACTORIES
        | ABSENCE_TOLERANT_FACTORIES
        | EXCLUDED_GPL_FACTORIES
    )
    missing = sorted(covered - set(FACTORY_PLUGIN))
    assert missing == [], f"factories with no plugin mapping: {missing}"


def test_linux_only_factories_are_never_shipped_on_windows() -> None:
    """VAAPI names live in `bridge.py`'s *config* vocabulary, where the
    encoder-remap slice translates them to Media Foundation. They are not
    factories in a Windows runtime, so mapping them to a plugin file would
    invent a dependency that cannot exist."""
    assert not (LINUX_ONLY_FACTORIES & REQUIRED_FACTORIES)
    assert not (LINUX_ONLY_FACTORIES & CONDITIONAL_FACTORIES)
    assert not (LINUX_ONLY_FACTORIES & ABSENCE_TOLERANT_FACTORIES)
    assert not (LINUX_ONLY_FACTORIES & set(FACTORY_PLUGIN))


def test_required_factory_count_includes_live_audio_processing_factories() -> None:
    """Captions, per-output normalization, and configured EAS filtering ship."""
    assert "appsink" in REQUIRED_FACTORIES
    assert FACTORY_PLUGIN["appsink"] == FACTORY_PLUGIN["appsrc"] == "gstapp.dll"
    assert "audioloudnorm" in REQUIRED_FACTORIES
    assert FACTORY_PLUGIN["audioloudnorm"] == "gstrsaudiofx.dll"
    assert "audiochebband" in REQUIRED_FACTORIES
    assert FACTORY_PLUGIN["audiochebband"] == "gstaudiofx.dll"
    assert FACTORY_PLUGIN["volume"] == "gstvolume.dll"
    # decodebin must retain PNG support after minimizing the FFmpeg codec set.
    assert "pngdec" in REQUIRED_FACTORIES
    assert FACTORY_PLUGIN["pngdec"] == "gstpng.dll"
    # AC-3 for the cable output: avenc_ac3 + ac3parse, and avdec_ac3 for AC-3 sources (#324).
    assert {"avenc_ac3", "avdec_ac3", "ac3parse"} <= REQUIRED_FACTORIES
    assert len(REQUIRED_FACTORIES) == 59


# --------------------------------------------------------------------------
# STAGED_OPTIONAL_FACTORIES -- S15 CG-lite / native-HLS (PR #88 disposition)
# --------------------------------------------------------------------------


def test_staged_optional_factories_are_the_three_cg_lite_plugins_pr88_named() -> None:
    """PR #88's body named exactly these as staged-but-not-required: the
    compositor (video mixer), the pango overlays (text/clock CG), and the
    native HLS sink -- present in the already-pinned `gstreamer-libs`/
    `gstreamer-plugins` 1.28.5 wheels, no new upstream artifact. `interpipe`
    is deliberately excluded: PR #88 recorded it as absent from the pinned
    wheels entirely (a RidgeRun-only artifact), so it cannot be staged
    additively the way these three can."""
    assert set(STAGED_OPTIONAL_FACTORIES) == {
        "compositor",
        "textoverlay",
        "clockoverlay",
        "hlssink3",
    }
    assert "interpipesrc" not in STAGED_OPTIONAL_FACTORIES
    assert "interpipesink" not in STAGED_OPTIONAL_FACTORIES


def test_staged_optional_factories_are_disjoint_from_required_factories() -> None:
    """REQUIRED_FACTORIES's own docstring is "the factories the product's
    pipelines cannot run without" -- these three are not that (no pipeline in
    `civiccast/egress/gst/engine.py` builds a graph with them today), so they
    must never silently widen REQUIRED_FACTORIES's meaning or its asserted
    count of 56."""
    assert not (STAGED_OPTIONAL_FACTORIES & REQUIRED_FACTORIES)
    assert not (STAGED_OPTIONAL_FACTORIES & EXCLUDED_GPL_FACTORIES)
    assert not (STAGED_OPTIONAL_FACTORIES & LINUX_ONLY_FACTORIES)


def test_staged_optional_factories_all_have_a_plugin_mapping() -> None:
    """Every staged factory must resolve to a real plugin file, matching the
    contract `test_every_policy_factory_has_a_plugin_mapping` already holds
    REQUIRED/CONDITIONAL/ABSENCE_TOLERANT/EXCLUDED to."""
    missing = sorted(STAGED_OPTIONAL_FACTORIES - set(FACTORY_PLUGIN))
    assert missing == [], f"staged-optional factories with no plugin mapping: {missing}"
    assert FACTORY_PLUGIN["compositor"] == "gstcompositor.dll"
    assert FACTORY_PLUGIN["textoverlay"] == FACTORY_PLUGIN["clockoverlay"] == "gstpango.dll"
    assert FACTORY_PLUGIN["hlssink3"] == "gsthlssink3.dll"


def test_select_plugin_seeds_includes_staged_optional_factories_when_present() -> None:
    """Mirrors exactly how `scripts/build_native_runtime_closure.py`'s
    `build()` folds STAGED_OPTIONAL_FACTORIES into its `required` set
    unconditionally (not gated on presence in `origins`, unlike
    CONDITIONAL_FACTORIES/ABSENCE_TOLERANT_FACTORIES) -- proving the pack
    actually carries the three CG-lite plugin files when they resolve."""
    origins = _origins(
        compositor=("lib/gstreamer-1.0/gstcompositor.dll", "gstreamer_libs"),
        textoverlay=("lib/gstreamer-1.0/gstpango.dll", "gstreamer_libs"),
        clockoverlay=("lib/gstreamer-1.0/gstpango.dll", "gstreamer_libs"),
        hlssink3=("lib/gstreamer-1.0/gsthlssink3.dll", "gstreamer_plugins"),
    )
    required = frozenset(origins) | STAGED_OPTIONAL_FACTORIES
    seeds = select_plugin_seeds(origins, required=required)
    assert seeds == (
        "lib/gstreamer-1.0/gstcompositor.dll",
        "lib/gstreamer-1.0/gsthlssink3.dll",
        "lib/gstreamer-1.0/gstpango.dll",
    )


def test_select_plugin_seeds_refuses_loudly_if_a_staged_optional_factory_goes_missing() -> None:
    """The pack-carries-it guarantee is fail-closed, not best-effort: if a
    future upstream bump ever drops one of these three plugins from the
    pinned wheels, the build must refuse with MissingPluginError -- never
    silently ship a tree missing the CG-lite plugin the product means to
    carry (this is why STAGED_OPTIONAL_FACTORIES is folded into `required`
    unconditionally in the build script, unlike CONDITIONAL_FACTORIES/
    ABSENCE_TOLERANT_FACTORIES, which are only added when already present in
    `origins`)."""
    origins = _origins(
        compositor=("lib/gstreamer-1.0/gstcompositor.dll", "gstreamer_libs"),
        # textoverlay/clockoverlay/hlssink3 deliberately absent from origins.
    )
    required = REQUIRED_FACTORIES | STAGED_OPTIONAL_FACTORIES
    with pytest.raises(MissingPluginError) as excinfo:
        select_plugin_seeds(origins, required=required)
    message = str(excinfo.value)
    assert "textoverlay" in message
    assert "clockoverlay" in message
    assert "hlssink3" in message


# --------------------------------------------------------------------------
# Seed selection + GPL policy
# --------------------------------------------------------------------------


def _origins(**mapping: tuple[str, str]) -> dict[str, tuple[str, str]]:
    """factory -> (plugin tree path, owning distribution)."""
    return dict(mapping)


def test_select_plugin_seeds_returns_deduplicated_sorted_paths() -> None:
    origins = _origins(
        mfh264enc=("lib/gstreamer-1.0/gstmediafoundation.dll", "gstreamer_plugins"),
        mfh265enc=("lib/gstreamer-1.0/gstmediafoundation.dll", "gstreamer_plugins"),
        openh264enc=("lib/gstreamer-1.0/gstopenh264.dll", "gstreamer_plugins"),
    )
    seeds = select_plugin_seeds(origins, required=frozenset(origins))
    assert seeds == (
        "lib/gstreamer-1.0/gstmediafoundation.dll",
        "lib/gstreamer-1.0/gstopenh264.dll",
    )


def test_select_plugin_seeds_refuses_a_gpl_distribution() -> None:
    """AC3 negative control: seeding x264enc into the required list makes the
    build REFUSE rather than quietly shipping GPL bytes."""
    origins = _origins(
        x264enc=("lib/gstreamer-1.0/gstx264.dll", "gstreamer_plugins_gpl_restricted"),
    )
    with pytest.raises(GplPolicyError) as excinfo:
        select_plugin_seeds(origins, required=frozenset({"x264enc"}))
    message = str(excinfo.value)
    assert "x264enc" in message
    assert "gstreamer_plugins_gpl_restricted" in message


def test_select_plugin_seeds_refuses_a_non_excluded_factory_from_a_gpl_distribution() -> None:
    """The distribution-level GPL gate, exercised on its own for the first time.

    Mutation testing exposed this: flipping `origin is not None` to `is None`
    on that branch disabled the whole check and every test still passed. The
    reason is that the only GPL test used `x264enc`, which the EARLIER
    excluded-factory branch catches first -- so the distribution branch had
    never once been reached.

    It matters because the two branches guard different things. The excluded
    set is a hardcoded list of encoder names we know about; the distribution
    check is the belt-and-braces catch for anything ELSE a GPL wheel might
    provide. `gsta52dec` is exactly that case: GPL-distributed, but not on the
    excluded-encoder list.
    """
    origins = _origins(
        gsta52dec=("lib/gstreamer-1.0/gsta52dec.dll", "gstreamer_plugins_gpl"),
    )
    with pytest.raises(GplPolicyError) as excinfo:
        select_plugin_seeds(origins, required=frozenset({"gsta52dec"}))
    message = str(excinfo.value)
    assert "gsta52dec" in message
    assert "gstreamer_plugins_gpl" in message


def test_select_plugin_seeds_refuses_an_excluded_factory_from_a_clean_distribution() -> None:
    """Defence in depth: even if an upstream rebuild moved x265enc into a
    non-GPL distribution, the factory itself stays excluded."""
    origins = _origins(
        x265enc=("lib/gstreamer-1.0/gstx265.dll", "gstreamer_plugins"),
    )
    with pytest.raises(GplPolicyError):
        select_plugin_seeds(origins, required=frozenset({"x265enc"}))


def test_select_plugin_seeds_raises_when_a_required_plugin_is_absent() -> None:
    with pytest.raises(MissingPluginError) as excinfo:
        select_plugin_seeds({}, required=frozenset({"openh264enc"}))
    assert "openh264enc" in str(excinfo.value)


def test_staging_a_gpl_distribution_at_all_is_refused() -> None:
    """Defence in depth at the INPUT boundary, not just at selection.

    Selection-time refusal only fires if a GPL factory is actually chosen. If
    someone re-added `gstreamer-plugins-gpl` to the lockfile, today's build
    would quietly stage it and simply not select it -- no GPL would ship, but
    the operator would get no signal that the no-GPL input contract had been
    broken. The lock is the thing under review; a silent pass there is how the
    next change ships x264enc for real.
    """
    with pytest.raises(GplPolicyError) as excinfo:
        assert_no_gpl_distributions(
            ["gstreamer_libs", "gstreamer_plugins", "gstreamer_plugins_gpl_restricted"]
        )
    assert "gstreamer_plugins_gpl_restricted" in str(excinfo.value)


def test_a_clean_distribution_set_is_accepted() -> None:
    assert_no_gpl_distributions(["gstreamer_libs", "gstreamer_plugins", "gstreamer_python"])


def test_an_unknown_distribution_is_refused_even_though_it_is_not_gpl() -> None:
    """CC-WS5-PKG-002, round 2: the gate was a DENYLIST and should be an ALLOWLIST.

    Round 1 asked for exact sets of authorised distributions. I implemented
    canonical-name matching against the KNOWN GPL names instead -- which fixed
    the reported symptom (name variants) and left the actual design wrong. The
    auditor demonstrated it: `civiccast-unknown-runtime` passed both gates and
    was allowed to contribute a plugin.

    Blocking the names you already know about is not a supply-chain control. A
    renamed, replaced or injected distribution is exactly the case that matters,
    and it is by definition not on any list of names you thought to write down.
    """
    with pytest.raises(UnauthorizedDistributionError) as excinfo:
        assert_authorized_distributions(["gstreamer_libs", "civiccast-unknown-runtime"])
    assert "civiccast-unknown-runtime" in str(excinfo.value)


def test_the_authorized_lock_set_is_accepted() -> None:
    """Deny-by-default must not mean deny-everything: the real staged set,
    including setuptools as a non-shipping transitive build input, is fine."""
    assert_authorized_distributions(
        [
            "gstreamer_libs",
            "gstreamer_plugins",
            "gstreamer_plugins_libs",
            "gstreamer_plugins_restricted",
            "gstreamer_python",
            "gstreamer_ext_runtime",
            "setuptools",
        ]
    )


def test_setuptools_may_be_staged_but_may_not_contribute_shipped_files() -> None:
    """setuptools is a build-time transitive input, not part of the runtime.

    Staging it is authorised; shipping a file from it is not. The two sets are
    deliberately different, because 'allowed to be present' and 'allowed into
    the product' are different questions.
    """
    assert "setuptools" in AUTHORIZED_STAGED_DISTRIBUTIONS
    assert "setuptools" not in AUTHORIZED_RUNTIME_DISTRIBUTIONS

    origins = {"appsrc": ("lib/gstreamer-1.0/gstapp.dll", "setuptools")}
    with pytest.raises(UnauthorizedDistributionError):
        select_plugin_seeds(origins, required=frozenset({"appsrc"}))


def test_selection_refuses_a_plugin_from_an_unauthorized_distribution() -> None:
    """The auditor's exact counterexample, locked in."""
    origins = {"appsrc": ("lib/gstapp.dll", "civiccast-unknown-runtime")}
    with pytest.raises(UnauthorizedDistributionError) as excinfo:
        select_plugin_seeds(origins, required=frozenset({"appsrc"}))
    assert "civiccast-unknown-runtime" in str(excinfo.value)


def test_gpl_gate_is_not_bypassed_by_an_equivalent_project_name_variant() -> None:
    """CC-WS5-PKG-002 (Codex r1, Critical).

    Both GPL gates compared raw distribution strings against a lowercase/
    underscore set, so a PEP 503-EQUIVALENT spelling walked straight past them.
    These are not exotic inputs: `GStreamer-Plugins-GPL`,
    `gstreamer-plugins-gpl` and `gstreamer_plugins_gpl` are the SAME project as
    far as Python packaging is concerned, and any of them can appear depending
    on which tool wrote the metadata.

    A gate whose whole purpose is "no GPL ever ships" must not be defeated by
    capitalisation.
    """
    variants = (
        "GStreamer_Plugins_GPL",
        "gstreamer-plugins-gpl",
        "GSTREAMER-PLUGINS-GPL",
        "gstreamer.plugins.gpl",
        "gstreamer__plugins--gpl",
    )
    for variant in variants:
        with pytest.raises(GplPolicyError, match="GPL"):
            assert_no_gpl_distributions(["gstreamer_libs", variant])


def test_gpl_selection_gate_also_normalises_the_distribution_name() -> None:
    """The same bypass existed on the selection-time gate, not just the input
    gate. Fixing only one would have left the other open."""
    origins = _origins(
        gsta52dec=("lib/gstreamer-1.0/gsta52dec.dll", "GStreamer-Plugins-GPL"),
    )
    with pytest.raises(GplPolicyError):
        select_plugin_seeds(origins, required=frozenset({"gsta52dec"}))


def test_a_clean_distribution_name_variant_is_still_accepted() -> None:
    """Normalisation must not turn into over-blocking: a non-GPL distribution
    spelled any equivalent way is fine."""
    assert_no_gpl_distributions(["GStreamer-Libs", "gstreamer.plugins", "GSTREAMER_PYTHON"])


def test_gpl_distributions_cover_both_upstream_gpl_wheels() -> None:
    assert set(GPL_DISTRIBUTIONS) == {
        "gstreamer_plugins_gpl",
        "gstreamer_plugins_gpl_restricted",
    }


# --------------------------------------------------------------------------
# PE import closure
# --------------------------------------------------------------------------


def test_pe_closure_walks_transitive_imports() -> None:
    graph = {
        "lib/gstreamer-1.0/gstopenh264.dll": ["gstvideo-1.0-0.dll", "KERNEL32.dll"],
        "bin/gstvideo-1.0-0.dll": ["glib-2.0-0.dll"],
        "bin/glib-2.0-0.dll": [],
    }
    in_tree = {
        "gstvideo-1.0-0.dll": "bin/gstvideo-1.0-0.dll",
        "glib-2.0-0.dll": "bin/glib-2.0-0.dll",
    }
    closure = resolve_pe_closure(
        ["lib/gstreamer-1.0/gstopenh264.dll"],
        imports_of=lambda path: graph[path],
        resolve=in_tree.get,
        system_allowlist=frozenset({"kernel32.dll"}),
    )
    assert closure == (
        "bin/glib-2.0-0.dll",
        "bin/gstvideo-1.0-0.dll",
        "lib/gstreamer-1.0/gstopenh264.dll",
    )


def test_pe_closure_tolerates_import_cycles() -> None:
    graph = {"bin/a.dll": ["b.dll"], "bin/b.dll": ["a.dll"]}
    in_tree = {"a.dll": "bin/a.dll", "b.dll": "bin/b.dll"}
    closure = resolve_pe_closure(
        ["bin/a.dll"],
        imports_of=lambda path: graph[path],
        resolve=in_tree.get,
        system_allowlist=frozenset(),
    )
    assert closure == ("bin/a.dll", "bin/b.dll")


def test_pe_closure_is_case_insensitive_about_system_dlls() -> None:
    """Windows PE import tables are inconsistently cased; the allowlist must
    not depend on the casing an upstream linker happened to emit."""
    closure = resolve_pe_closure(
        ["bin/a.dll"],
        imports_of=lambda path: ["KeRnEl32.DLL", "api-ms-win-crt-heap-l1-1-0.dll"],
        resolve=lambda name: None,
        system_allowlist=frozenset({"kernel32.dll", "api-ms-win-crt-heap-l1-1-0.dll"}),
    )
    assert closure == ("bin/a.dll",)


def test_pe_closure_keeps_walking_imports_after_skipping_a_system_dll() -> None:
    """A system DLL must be SKIPPED, not treated as end-of-list.

    Mutation testing found this: turning the allowlist `continue` into a
    `break` left every existing test green, because no test had an allowlisted
    import followed by a real one. In production that mutation would silently
    drop every dependency listed after the first system DLL -- the tree would
    build and hash perfectly and then fail to load on the operator's machine.
    """
    graph = {
        "bin/a.dll": ["KERNEL32.dll", "real-dep.dll"],
        "bin/real-dep.dll": [],
    }
    closure = resolve_pe_closure(
        ["bin/a.dll"],
        imports_of=lambda path: graph[path],
        resolve={"real-dep.dll": "bin/real-dep.dll"}.get,
        system_allowlist=frozenset({"kernel32.dll"}),
    )
    assert closure == ("bin/a.dll", "bin/real-dep.dll")


def test_pe_closure_keeps_walking_after_meeting_an_already_visited_node() -> None:
    """Re-visiting a node must be SKIPPED, not treated as end-of-walk.

    The second mutation survivor: `if path in closure: continue` -> `break`.
    Reaching it needs a node queued twice before either copy is popped, and
    something still queued behind it -- which no previous test produced, so the
    guard was never exercised at all. With `break`, `bin/z.dll` below is never
    reached and silently vanishes from the closure.
    """
    graph = {
        "bin/a.dll": ["z.dll", "d.dll", "x.dll"],
        "bin/x.dll": ["d.dll"],
        "bin/d.dll": [],
        "bin/z.dll": [],
    }
    index = {"z.dll": "bin/z.dll", "d.dll": "bin/d.dll", "x.dll": "bin/x.dll"}
    closure = resolve_pe_closure(
        ["bin/a.dll"],
        imports_of=lambda path: graph[path],
        resolve=index.get,
        system_allowlist=frozenset(),
    )
    assert closure == ("bin/a.dll", "bin/d.dll", "bin/x.dll", "bin/z.dll")


def test_pe_closure_refuses_an_unresolvable_non_system_import() -> None:
    """Halt trigger: an unknown-provenance DLL is never shipped and never
    silently dropped -- a missing dependency here is a runtime crash on the
    operator's machine."""
    with pytest.raises(UnknownProvenanceError) as excinfo:
        resolve_pe_closure(
            ["bin/a.dll"],
            imports_of=lambda path: ["mystery-codec.dll"],
            resolve=lambda name: None,
            system_allowlist=frozenset({"kernel32.dll"}),
        )
    message = str(excinfo.value)
    assert "mystery-codec.dll" in message
    assert "bin/a.dll" in message, "the error must name the importer, not just the missing DLL"


def test_pe_closure_reports_every_unresolved_import_not_just_the_first() -> None:
    with pytest.raises(UnknownProvenanceError) as excinfo:
        resolve_pe_closure(
            ["bin/a.dll"],
            imports_of=lambda path: ["one.dll", "two.dll"],
            resolve=lambda name: None,
            system_allowlist=frozenset(),
        )
    message = str(excinfo.value)
    assert "one.dll" in message
    assert "two.dll" in message


class TestHardwareGatedClassification:
    """The shared hardware-gating rule (promoted from the closure verifier
    when candidate run 31190955761's installed smoke hard-required
    GPU-gated factories on a GPU-less runner)."""

    def test_gated_factories_with_shipped_plugins_are_excused_and_reported(self) -> None:
        from civiccast.native.runtime_closure import classify_missing_factories

        gated, genuine = classify_missing_factories(["d3d12h264dec", "mfh265enc"])
        assert gated == ("d3d12h264dec", "mfh265enc")
        assert genuine == ()

    def test_gated_factory_with_a_missing_plugin_file_stays_a_genuine_miss(self) -> None:
        from civiccast.native.runtime_closure import classify_missing_factories

        gated, genuine = classify_missing_factories(
            ["d3d12h264dec", "mfh265enc"],
            plugin_file_missing=frozenset({"d3d12h264dec"}),
        )
        assert gated == ("mfh265enc",)
        assert genuine == ("d3d12h264dec",)

    def test_non_gated_missing_factory_is_always_genuine(self) -> None:
        from civiccast.native.runtime_closure import classify_missing_factories

        gated, genuine = classify_missing_factories(["mpegtsmux"])
        assert gated == ()
        assert genuine == ("mpegtsmux",)

    def test_installed_smoke_uses_the_shared_classifier_not_a_hard_requirement(self) -> None:
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[2] / "civiccast/native/installed_gstreamer_smoke.py"
        ).read_text(encoding="utf-8")
        assert "classify_missing_factories" in source
        assert "hardware_gated_absent" in source


class TestNonFactoryPlugins:
    """gsttypefindfunctions.dll ships even though no element factory names it
    (candidate run 31205696163: a byte-valid MPEG-TS was undiscoverable in
    the installed tree because the typefinder plugin was never seeded)."""

    def test_typefind_functions_plugin_is_declared(self) -> None:
        from civiccast.native.runtime_closure import NON_FACTORY_PLUGINS

        assert "gsttypefindfunctions.dll" in NON_FACTORY_PLUGINS

    def test_non_factory_plugins_are_disjoint_from_the_factory_table(self) -> None:
        # A plugin that gains an element factory should move to
        # FACTORY_PLUGIN and be selected the normal way -- the two
        # mechanisms must never both claim the same file.
        from civiccast.native.runtime_closure import FACTORY_PLUGIN, NON_FACTORY_PLUGINS

        assert not (NON_FACTORY_PLUGINS & set(FACTORY_PLUGIN.values()))

    def test_builder_seeds_non_factory_plugins_and_fails_closed_when_absent(self, tmp_path) -> None:
        import importlib.util
        import sys
        from pathlib import Path

        script = Path(__file__).resolve().parents[2] / "scripts" / "build_native_runtime_closure.py"
        spec = importlib.util.spec_from_file_location("bnrc_for_test", script)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

        stage = tmp_path / "stage"
        plugin = stage / "gstreamer_plugins" / "lib" / "gstreamer-1.0" / "gsttypefindfunctions.dll"
        plugin.parent.mkdir(parents=True)
        plugin.write_bytes(b"fixture")
        seeds = module.non_factory_plugin_seeds(stage)
        assert seeds == ("gstreamer_plugins/lib/gstreamer-1.0/gsttypefindfunctions.dll",)

        plugin.unlink()
        with pytest.raises(RuntimeError, match=r"gsttypefindfunctions\.dll"):
            module.non_factory_plugin_seeds(stage)
