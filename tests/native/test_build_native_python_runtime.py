# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Fail-closed source inputs and bounded upstream CPython build overrides."""

from __future__ import annotations

import hashlib
import importlib.util
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build_native_python_runtime.py"
spec = importlib.util.spec_from_file_location("build_native_python_runtime", SCRIPT)
assert spec is not None and spec.loader is not None
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def test_runtime_inputs_pin_security_source_and_modern_external_dependencies():
    assert builder.VERSION == "3.12.16"
    assert builder.SOURCE["url"].endswith("/Python-3.12.16.tgz")
    assert builder.SOURCE["sha256"] == (
        "710c3e75dac8b2f258d9cd0f76e5c4c2e9175fac9e249c98901552f7cf33b7dd"
    )
    assert {item["name"]: item["version"] for item in builder.DEPENDENCIES} == {
        "bzip2": "1.0.8",
        "libffi": "3.4.4",
        "openssl": "3.5.9",
        "sqlite": "3.54.1",
        "zlib": "1.3.2",
        "xz": "5.8.3",
    }
    for item in [builder.SOURCE, *builder.DEPENDENCIES]:
        assert len(item["sha256"]) == 64 and item["license"]


def test_cached_source_hash_mismatch_never_reaches_extraction(tmp_path):
    path = tmp_path / "source-1.zip"
    path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="cached input SHA256 mismatch"):
        builder.fetch_input(
            {
                "name": "source",
                "version": "1",
                "url": "https://example.org/a.zip",
                "sha256": hashlib.sha256(b"reviewed").hexdigest(),
            },
            tmp_path,
        )


def test_archive_reuse_checks_original_source_contents(tmp_path):
    archive = tmp_path / "input.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("source/PCbuild/build.bat", "reviewed upstream")
    output = tmp_path / "source"
    builder.unpack_input(archive, output)
    builder.unpack_input(archive, output)
    assert (output / "PCbuild" / "build.bat").read_text() == "reviewed upstream"
    (output / "PCbuild" / "build.bat").write_text("unreviewed local patch")
    with pytest.raises(ValueError, match="modified runtime source cache"):
        builder.unpack_input(archive, output)


def test_upstream_generated_crlf_text_can_resume_without_accepting_modified_code(tmp_path):
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("source/Python/generated.c", b"/* upstream */\nint x;\n")
    target = tmp_path / "source"
    builder.unpack_input(archive, target)
    generated = target / "Python" / "generated.c"
    generated.write_bytes(b"/* upstream */\r\nint x;\r\n")
    builder.unpack_input(archive, target)
    generated.write_bytes(b"/* upstream */\r\nint changed;\r\n")
    with pytest.raises(ValueError, match="modified runtime source cache"):
        builder.unpack_input(archive, target)


@pytest.mark.parametrize("name", ["source/../../escape", "C:/escape", "source/..\\escape"])
def test_source_extraction_refuses_escaping_paths(tmp_path, name):
    archive = tmp_path / "input.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(name, "unsafe")
    with pytest.raises(ValueError, match="unsafe runtime source archive path"):
        builder.unpack_input(archive, tmp_path / "output")
    assert not (tmp_path / "escape").exists()


def test_compile_override_caps_workers_and_uses_upstream_liblzma(tmp_path):
    path = tmp_path / "compile.props"
    builder.write_compile_overrides(path)
    root = ElementTree.parse(path).getroot()
    namespace = {"m": "http://schemas.microsoft.com/developer/msbuild/2003"}
    assert root.find(".//m:MultiProcessorCompilation", namespace).text == "false"
    removal = root.find(".//m:ProjectReference", namespace)
    assert removal.attrib == {"Remove": "liblzma.vcxproj"}
    group = root.find("m:ItemGroup", namespace)
    assert group.attrib["Condition"] == "'$(MSBuildProjectName)' == '_lzma'"


def test_dependency_overrides_escape_paths_and_avoid_upstream_stale_defaults(tmp_path):
    path = tmp_path / "deps.props"
    deps = tmp_path / "deps & dependencies"
    builder.write_overrides(path, deps)
    root = ElementTree.parse(path).getroot()
    namespace = {"m": "http://schemas.microsoft.com/developer/msbuild/2003"}
    assert root.find(".//m:opensslOutDir", namespace).text == str(deps / "openssl" / "amd64") + "\\"
    assert root.find(".//m:sqlite3Dir", namespace).text == str(deps / "sqlite-3.54.1.0") + "\\"


def _write_expat_fixture(root: Path) -> None:
    (root / "lib").mkdir(parents=True)
    (root / "COPYING").write_text("MIT\n")
    (root / "lib" / "expat.h").write_text("#define XML_EXPAT 1\n")
    (root / "lib" / "expat_external.h").write_text(
        "#ifndef Expat_External_INCLUDED\n"
        "#  define Expat_External_INCLUDED 1\n\n"
        "/* External API definitions */\n"
    )


def test_vendor_expat_uses_upstream_bytes_and_preserves_cpython_customizations(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(builder, "EXPAT_LIB_FILES", ("lib/expat.h", "lib/expat_external.h"))
    expat = tmp_path / "expat"
    _write_expat_fixture(expat)
    modules = tmp_path / "Modules" / "expat"
    modules.mkdir(parents=True)
    config = b"/* CPython config */\n"
    namespace = b"/* CPython namespace */\n"
    (modules / "expat_config.h").write_bytes(config)
    (modules / "pyexpatns.h").write_bytes(namespace)

    result = builder.vendor_expat(expat, modules)

    assert (modules / "expat.h").read_text() == "#define XML_EXPAT 1\n"
    assert (modules / "expat_config.h").read_bytes() == config
    assert (modules / "pyexpatns.h").read_bytes() == namespace
    external = (modules / "expat_external.h").read_text()
    assert external.count('#include "pyexpatns.h"') == 1
    assert "#  define Expat_External_INCLUDED 1\n/* Namespace" in external
    assert (
        result["files"]["expat.h"]
        == hashlib.sha256((expat / "lib" / "expat.h").read_bytes()).hexdigest()
    )
    assert result["config_sha256"] == hashlib.sha256(config).hexdigest()


def test_derived_expat_vendoring_is_reused_without_mutating_pristine(tmp_path, monkeypatch):
    monkeypatch.setattr(builder, "EXPAT_LIB_FILES", ("lib/expat.h", "lib/expat_external.h"))
    expat = tmp_path / "expat"
    _write_expat_fixture(expat)
    pristine = tmp_path / "pristine"
    _write_project_fixtures(pristine)
    pristine_expat = pristine / "Modules" / "expat"
    pristine_expat.mkdir(parents=True)
    for name, data in {
        "expat_config.h": b"/* CPython config */\n",
        "pyexpatns.h": b"/* CPython namespace */\n",
        "expat_external.h": b"#ifndef Expat_External_INCLUDED\n"
        b"#  define Expat_External_INCLUDED 1\n\n"
        b"/* External API definitions */\n",
    }.items():
        (pristine_expat / name).write_bytes(data)
    pristine_external = (pristine_expat / "expat_external.h").read_bytes()

    derived = tmp_path / "derived"
    builder.copy_source_tree(pristine, derived)
    modules = derived / "Modules" / "expat"
    first = builder.vendor_expat(expat, modules)
    second = builder.vendor_expat(expat, modules)

    assert first == second
    assert (modules / "expat_external.h").read_text().count('#include "pyexpatns.h"') == 1
    assert (pristine_expat / "expat_external.h").read_bytes() == pristine_external
    (derived / "marker").write_text("kept")
    builder.copy_source_tree(pristine, derived)
    assert (derived / "marker").read_text() == "kept"


def _write_project_fixtures(root: Path, newline: bytes = b"\n") -> None:
    projects = root / "PCbuild"
    projects.mkdir(parents=True)
    content = newline.join(
        [b"<Project>", b'    <ClCompile Include="..\\Modules\\expat\\xcs.c" />', b"</Project>", b""]
    )
    for name in ("pyexpat.vcxproj", "_elementtree.vcxproj"):
        (projects / name).write_bytes(content)


@pytest.mark.parametrize("mutation", ["changed", "missing", "config"])
def test_derived_source_rejects_modified_or_missing_original_input(tmp_path, mutation):
    pristine, derived = tmp_path / "pristine", tmp_path / "derived"
    _write_project_fixtures(pristine)
    original = pristine / "Modules" / "expat" / "expat_config.h"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"/* original */\n")
    ordinary = pristine / "Modules" / "pyexpat.c"
    ordinary.write_bytes(b"/* ordinary source */\n")
    builder.copy_source_tree(pristine, derived)
    target = derived / (original if mutation == "config" else ordinary).relative_to(pristine)
    if mutation == "missing":
        target.unlink()
    else:
        target.write_bytes(b"/* changed */\n")
    with pytest.raises(ValueError, match="modified derived runtime source"):
        builder.copy_source_tree(pristine, derived)


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_windows_expat_projects_compile_entropy_once_and_reject_tampering(tmp_path, newline):
    pristine, derived = tmp_path / "pristine", tmp_path / "derived"
    _write_project_fixtures(pristine, newline)
    originals = {p.name: p.read_bytes() for p in (pristine / "PCbuild").iterdir()}
    builder.copy_source_tree(pristine, derived)
    marker = derived / "PCbuild" / "generated.obj"
    marker.write_bytes(b"preserve build output")
    builder.copy_source_tree(pristine, derived)
    for name, original in originals.items():
        content = (derived / "PCbuild" / name).read_bytes()
        assert content.count(b"expat\\random_rand_s.c") == 1
        assert content.count(b"expat\\xcs.c") == 1
        assert (
            content.replace(
                b'    <ClCompile Include="..\\Modules\\expat\\random_rand_s.c" />' + newline, b""
            )
            == original
        )
        assert (pristine / "PCbuild" / name).read_bytes() == original
    assert marker.read_bytes() == b"preserve build output"
    (derived / "PCbuild" / "pyexpat.vcxproj").write_bytes(b"tampered project")
    with pytest.raises(ValueError, match="modified derived runtime source"):
        builder.copy_source_tree(pristine, derived)
