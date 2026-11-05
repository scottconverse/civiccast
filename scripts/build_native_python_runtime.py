#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Build pinned CPython security sources with upstream PCbuild and embed layout.

Python 3.12 security releases have no official Windows embeddable download.
This produces a local artifact and provenance, never invents an upstream binary
URL. Release integration must pin the resulting zip's exact SHA256 and size.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any
from xml.sax.saxutils import escape

VERSION = "3.12.16"
BUILDER_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
SOURCE = {
    "name": "cpython",
    "version": VERSION,
    "license": "PSF-2.0",
    "url": "https://www.python.org/ftp/python/3.12.16/Python-3.12.16.tgz",
    "sha256": "710c3e75dac8b2f258d9cd0f76e5c4c2e9175fac9e249c98901552f7cf33b7dd",
}
EXPAT_SOURCE = {
    "name": "expat",
    "version": "2.10.0",
    "license": "MIT",
    "url": "https://github.com/libexpat/libexpat/releases/download/R_2_10_0/expat-2.10.0.tar.gz",
    "sha256": "5466a341f9c692ef7a958a6881311be597f8e1ce345984cf78414f54793dea4c",
}
EXPAT_LIB_FILES = (
    "lib/libexpat.map.in",
    "lib/Makefile.in",
    "lib/xmltok.h",
    "lib/xmlrole.h",
    "lib/latin1tab.h",
    "lib/random_arc4random_buf.h",
    "lib/random_getentropy.c",
    "lib/xmltok_impl.h",
    "lib/random_dev_urandom.c",
    "lib/xcs.c",
    "lib/siphash.h",
    "lib/nametab.h",
    "lib/libexpat.def.cmake",
    "lib/random_dev_urandom.h",
    "lib/xcs.h",
    "lib/expat_external.h",
    "lib/Makefile.am",
    "lib/random_rand_s.h",
    "lib/utf8tab.h",
    "lib/random_getrandom.c",
    "lib/xmlrole.c",
    "lib/random_arc4random.h",
    "lib/expat.h",
    "lib/winconfig.h",
    "lib/random_arc4random.c",
    "lib/random_rand_s.c",
    "lib/entities.h",
    "lib/memory_sanitizer.h",
    "lib/random_getentropy.h",
    "lib/asciitab.h",
    "lib/random_arc4random_buf.c",
    "lib/fallthrough.h",
    "lib/hash_table.h",
    "lib/xmltok.c",
    "lib/iasciitab.h",
    "lib/xmltok_ns.c",
    "lib/internal.h",
    "lib/xmlparse.c",
    "lib/xmltok_impl.c",
    "lib/random_getrandom.h",
    "lib/ascii.h",
)
# PSF dependency mirrors use immutable commits, not mutable tag downloads.
# zlib's hash is publisher-listed; SQLite's sqlite3.c is independently checked
# against the published release SHA3 below. Other hashes bind reviewed upstream
# archive bytes. No get_externals.bat/network fetch runs during compilation.
DEPENDENCIES = [
    {
        "name": "bzip2",
        "version": "1.0.8",
        "license": "bzip2-1.0.6",
        "url": "https://codeload.github.com/python/cpython-source-deps/zip/05301997b2f9590f49c672cf3dfd3d3dfa7ad521",
        "sha256": "44b15bede52b32f8673a2976621a847258b041243c003e30f4d9a05214785fe5",
    },
    {
        "name": "libffi",
        "version": "3.4.4",
        "license": "MIT",
        "url": "https://codeload.github.com/python/cpython-bin-deps/zip/94cb9a1c7feb608adf2b9f8fe2dbd6925ffbf90d",
        "sha256": "6b0f53490e0dd958708bff32df689cf2163c214147fcb8edb76a403fa6a877ca",
    },
    {
        "name": "openssl",
        "version": "3.5.9",
        "license": "Apache-2.0",
        "url": "https://codeload.github.com/python/cpython-bin-deps/zip/ddb46a415b4995440723183e644f8d2c346567a2",
        "sha256": "0dbf56dcce0ab4054e6192ae488d7d439f678cceca858810de990be51bb80335",
    },
    {
        "name": "sqlite",
        "version": "3.54.1",
        "license": "blessing",
        "url": "https://sqlite.org/2026/sqlite-amalgamation-3540100.zip",
        "sha256": "0a9c8e34352dad46006aa82836d5383bff9dc6bc9ce5a60553c05aa30e6ad1f9",
    },
    {
        "name": "zlib",
        "version": "1.3.2",
        "license": "Zlib",
        "url": "https://zlib.net/zlib132.zip",
        "sha256": "e8bf55f3017aa181690990cb58a994e77885da140609fc8f94abe9b65d2cae28",
    },
    {
        "name": "xz",
        "version": "5.8.3",
        "license": "0BSD",
        "url": "https://github.com/tukaani-project/xz/releases/download/v5.8.3/xz-5.8.3.tar.gz",
        "sha256": "3d3a1b973af218114f4f889bbaa2f4c037deaae0c8e815eec381c3d546b974a0",
    },
]
SQLITE_C_SHA3 = "e2b869d3003b33c942b7dd7063604fbc53553e1b66609570df39b9b7b558ee81"
STATIC_COMPONENTS = [
    {
        "name": "libexpat",
        "version": "2.10.0",
        "license": "MIT",
        "source": "https://github.com/libexpat/libexpat/releases/download/R_2_10_0/expat-2.10.0.tar.gz",
        "sha256": "5466a341f9c692ef7a958a6881311be597f8e1ce345984cf78414f54793dea4c",
    },
    {"name": "mpdecimal", "version": "2.5.1", "license": "BSD-2-Clause", "source": "cpython"},
]


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def fetch_input(item: dict[str, str], cache: Path) -> Path:
    suffix = ".tar.gz" if item["url"].endswith((".tgz", ".tar.gz")) else ".zip"
    path = cache / f"{item['name']}-{item['version']}{suffix}"
    cache.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        partial = path.with_suffix(path.suffix + ".partial")
        try:
            urllib.request.urlretrieve(item["url"], partial)
            if sha256(partial) != item["sha256"]:
                raise ValueError(f"{item['name']} input SHA256 mismatch")
            partial.replace(path)
        finally:
            partial.unlink(missing_ok=True)
    if sha256(path) != item["sha256"]:
        raise ValueError(f"{item['name']} cached input SHA256 mismatch")
    return path


def _member_path(name: str) -> tuple[str, ...]:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name or "\0" in name:
        raise ValueError("unsafe runtime source archive path")
    return path.parts


def unpack_input(archive: Path, target: Path) -> None:
    """Extract only regular files, stripping one upstream archive root.

    Existing source files must remain byte-identical, enabling incremental
    compiler recovery without trusting a modified cached source tree.
    """
    archive_root: str | None = None

    def write(name: str, data: bytes) -> None:
        nonlocal archive_root
        parts = _member_path(name)
        if not parts:
            raise ValueError("runtime source archive has no root")
        archive_root = archive_root or parts[0]
        if parts[0] != archive_root or len(parts) < 2:
            raise ValueError("runtime source archive must contain one root directory")
        output = target.joinpath(*parts[1:])
        if any(parent.is_symlink() for parent in (output, *output.parents)):
            raise ValueError("runtime source target contains a symbolic link")
        if output.exists():
            actual = output.read_bytes()
            # Upstream Windows generators rewrite checked-in text with CRLF.
            # Permit that representation change, never changed source text or
            # altered binary dependencies, when recovering an incremental build.
            same_windows_text = output.suffix in {".c", ".h", ".py"} and actual.replace(
                b"\r\n", b"\n"
            ) == data.replace(b"\r\n", b"\n")
            if actual != data and not same_windows_text:
                raise ValueError(f"modified runtime source cache: {output}")
        else:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(data)

    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as bundle:
            for entry in bundle.infolist():
                _member_path(entry.filename)
                if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("runtime source archive contains a symbolic link")
                if not entry.is_dir():
                    write(entry.filename, bundle.read(entry))
    else:
        with tarfile.open(archive) as bundle:
            for entry in bundle:
                _member_path(entry.name)
                if entry.isdir():
                    continue
                if not entry.isfile():
                    raise ValueError("runtime source archive contains a non-regular member")
                stream = bundle.extractfile(entry)
                assert stream is not None
                with stream:
                    write(entry.name, stream.read())


EXPAT_WINDOWS_PROJECTS = ("PCbuild/pyexpat.vcxproj", "PCbuild/_elementtree.vcxproj")


def _expat_windows_project(original: bytes) -> bytes:
    """Add Expat's Windows entropy implementation to an original project."""
    newline = b"\r\n" if b"\r\n" in original else b"\n"
    anchor = b'    <ClCompile Include="..\\Modules\\expat\\xcs.c" />' + newline
    if original.count(anchor) != 1 or b"random_rand_s.c" in original:
        raise ValueError("CPython Expat Windows project source anchor changed")
    addition = b'    <ClCompile Include="..\\Modules\\expat\\random_rand_s.c" />' + newline
    return original.replace(anchor, anchor + addition)


def copy_source_tree(pristine: Path, derived: Path) -> None:
    """Retain generated outputs while validating every original source input."""
    vendored = {PurePosixPath(name).name for name in EXPAT_LIB_FILES} | {"COPYING"}
    if not derived.exists():
        shutil.copytree(pristine, derived)
    else:
        for original in pristine.rglob("*"):
            if not original.is_file():
                continue
            relative = original.relative_to(pristine)
            target = derived / relative
            if any(path.is_symlink() for path in (target, *target.parents)) or not target.is_file():
                raise ValueError(f"modified derived runtime source: {target}")
            # These files are immediately replaced from the verified Expat input.
            if relative.parent.as_posix() == "Modules/expat" and relative.name in vendored:
                continue
            expected, actual = original.read_bytes(), target.read_bytes()
            same_windows_text = target.suffix in {".c", ".h", ".py"} and actual.replace(
                b"\r\n", b"\n"
            ) == expected.replace(b"\r\n", b"\n")
            same_project = (
                relative.as_posix() in EXPAT_WINDOWS_PROJECTS
                and actual == _expat_windows_project(expected)
            )
            if actual != expected and not same_windows_text and not same_project:
                raise ValueError(f"modified derived runtime source: {target}")
    for relative in EXPAT_WINDOWS_PROJECTS:
        _write_bytes_if_changed(
            derived / relative, _expat_windows_project((pristine / relative).read_bytes())
        )


def tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _write_bytes_if_changed(path: Path, data: bytes) -> None:
    if not path.exists() or path.read_bytes() != data:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def vendor_expat(expat_root: Path, modules_expat: Path) -> dict[str, Any]:
    """Vendor pinned upstream libexpat files into a derived CPython tree.

    CPython's expat_config.h and pyexpatns.h are local customizations and are
    deliberately retained. Upstream expat_external.h must include the
    namespace header exactly as CPython's bundled copy does.
    """
    expected = set(EXPAT_LIB_FILES)
    lib = expat_root / "lib"
    actual = {path.relative_to(expat_root).as_posix() for path in lib.rglob("*") if path.is_file()}
    if actual != expected:
        raise ValueError(
            "Expat 2.10.0 lib file set mismatch: "
            f"missing={sorted(expected - actual)} extra={sorted(actual - expected)}"
        )
    files: dict[str, str] = {}
    for relative in EXPAT_LIB_FILES:
        source_file = expat_root / relative
        target_file = modules_expat / PurePosixPath(relative).name
        data = source_file.read_bytes()
        _write_bytes_if_changed(target_file, data)
        files[target_file.name] = hashlib.sha256(data).hexdigest()
    _write_bytes_if_changed(modules_expat / "COPYING", (expat_root / "COPYING").read_bytes())

    external = modules_expat / "expat_external.h"
    text = external.read_text(encoding="utf-8")
    include = '#include "pyexpatns.h"'
    marker = '''/* Namespace external symbols to allow multiple libexpat version to
   co-exist. */
#include "pyexpatns.h"'''
    if include not in text:
        anchor = """#ifndef Expat_External_INCLUDED
#  define Expat_External_INCLUDED 1

/* External API definitions */"""
        replacement = """#ifndef Expat_External_INCLUDED
#  define Expat_External_INCLUDED 1
/* Namespace external symbols to allow multiple libexpat version to
   co-exist. */
#include "pyexpatns.h"

/* External API definitions */"""
        if text.count(anchor) != 1:
            raise ValueError("Expat expat_external.h insertion anchor is not unique")
        text = text.replace(anchor, replacement)
        external.write_text(text, encoding="utf-8")
    elif text.count(include) != 1 or text.count(marker) != 1:
        raise ValueError("Expat expat_external.h namespace include is malformed")
    files[external.name] = sha256(external)
    return {
        "files": files,
        "config_sha256": sha256(modules_expat / "expat_config.h"),
        "namespace_sha256": sha256(modules_expat / "pyexpatns.h"),
        "external_sha256": sha256(external),
    }


def run(command: list[str], *, log: Path, env: dict[str, str] | None = None) -> None:
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(command) + "\n")
        handle.flush()
        subprocess.run(
            command,
            stdout=handle,
            stderr=subprocess.STDOUT,
            check=True,
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )


def write_overrides(path: Path, deps: Path) -> None:
    properties = {
        "bz2Dir": deps / "bzip2",
        "libffiDir": deps / "libffi",
        "opensslOutDir": deps / "openssl" / "amd64",
        # Upstream sqlite3.vcxproj derives its resource version from the
        # directory suffix, requiring four numeric components.
        "sqlite3Dir": deps / "sqlite-3.54.1.0",
        "zlibDir": deps / "zlib",
        "lzmaDir": deps / "xz",
    }
    content = [
        '<Project xmlns="http://schemas.microsoft.com/developer/msbuild/2003">',
        "<PropertyGroup>",
    ]
    content.extend(f"<{key}>{escape(str(value))}\\</{key}>" for key, value in properties.items())
    content.extend(["</PropertyGroup>", "</Project>"])
    _write_if_changed(path, "\n".join(content))


def _write_if_changed(path: Path, text: str) -> None:
    # Changing an imported .props mtime rebuilds every CPython C source.
    # Preserve valid compile evidence when resuming unchanged configuration.
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")


def write_compile_overrides(path: Path) -> None:
    # Upstream3.12 hardcodes /MP (unbounded compiler workers). Disable it so
    # MSBuild's /m:2 is the actual aggregate cap. Modern liblzma is built by
    # its own upstream CMake project; do not compile the obsolete5.2 source list.
    _write_if_changed(
        path,
        """<Project xmlns="http://schemas.microsoft.com/developer/msbuild/2003">
<ItemDefinitionGroup><ClCompile><MultiProcessorCompilation>false</MultiProcessorCompilation></ClCompile></ItemDefinitionGroup>
<ItemGroup Condition="'$(MSBuildProjectName)' == '_lzma'">
<ProjectReference Remove="liblzma.vcxproj" />
</ItemGroup>
</Project>
""",
    )


def build(*, work: Path, output: Path, visual_studio: Path, cmake: str) -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("the native CPython runtime is built on Windows with MSVC")
    work, output, visual_studio = work.resolve(), output.resolve(), visual_studio.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite runtime artifact: {output}")
    work.mkdir(parents=True, exist_ok=True)
    log = work / "python-runtime-build.log"
    inputs = [SOURCE, *DEPENDENCIES, EXPAT_SOURCE]
    paths: dict[str, Path] = {}
    pristine = work / "pristine" / "cpython"
    unpack_input(fetch_input(SOURCE, work / "cache"), pristine)
    for item in DEPENDENCIES:
        archive = fetch_input(item, work / "cache")
        directory = "sqlite-3.54.1.0" if item["name"] == "sqlite" else item["name"]
        destination = work / f"deps/{directory}"
        unpack_input(archive, destination)
        paths[item["name"]] = destination
    expat_archive = fetch_input(EXPAT_SOURCE, work / "cache")
    expat_dir = work / "expat"
    unpack_input(expat_archive, expat_dir)
    paths["expat"] = expat_dir
    source = work / "source"
    copy_source_tree(pristine, source)
    paths["cpython"] = source
    modules_expat = source / "Modules" / "expat"
    local_names = ("expat_config.h", "pyexpatns.h")
    local_hashes = {name: sha256(modules_expat / name) for name in local_names}
    for name in local_names:
        if local_hashes[name] != sha256(pristine / "Modules" / "expat" / name):
            raise ValueError(f"derived CPython local Expat customization changed: {name}")
    bundled_original_hashes = tree_hashes(pristine / "Modules" / "expat")
    vendored_expat = vendor_expat(paths["expat"], modules_expat)
    vendored_expat["windows_projects"] = {
        relative: sha256(source / relative) for relative in EXPAT_WINDOWS_PROJECTS
    }
    if {name: sha256(modules_expat / name) for name in local_names} != local_hashes:
        raise ValueError("vendoring modified a CPython Expat local customization")
    if hashlib.sha3_256((paths["sqlite"] / "sqlite3.c").read_bytes()).hexdigest() != SQLITE_C_SHA3:
        raise ValueError("SQLite source differs from its published SHA3 release fingerprint")
    deps = work / "deps"
    msbuild = visual_studio / "MSBuild" / "Current" / "Bin" / "MSBuild.exe"
    if not msbuild.is_file():
        raise FileNotFoundError(msbuild)
    environment = dict(os.environ)
    environment["CL"] = ""
    environment["_CL_"] = ""
    environment["LINK"] = ""
    environment["CMAKE_BUILD_PARALLEL_LEVEL"] = "2"
    xz_build = work / "xz-build"
    run(
        [
            cmake,
            "-S",
            str(paths["xz"]),
            "-B",
            str(xz_build),
            "-G",
            "Visual Studio 18 2026",
            "-A",
            "x64",
            f"-DCMAKE_GENERATOR_INSTANCE={visual_studio}",
            "-DBUILD_SHARED_LIBS=OFF",
            "-DBUILD_TESTING=OFF",
            "-DXZ_NLS=OFF",
            "-DXZ_TOOL_XZ=OFF",
            "-DXZ_TOOL_XZDEC=OFF",
            "-DXZ_TOOL_LZMADEC=OFF",
            "-DXZ_TOOL_LZMAINFO=OFF",
            "-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreadedDLL",
        ],
        log=log,
        env=environment,
    )
    run(
        [
            cmake,
            "--build",
            str(xz_build),
            "--config",
            "Release",
            "--target",
            "liblzma",
            "--parallel",
            "2",
        ],
        log=log,
        env=environment,
    )
    build_dir = source / "PCbuild" / "amd64"
    build_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(xz_build / "Release" / "lzma.lib", build_dir / "liblzma.lib")
    props, compile_props = work / "dependencies.props", work / "compile.props"
    write_overrides(props, deps)
    write_compile_overrides(compile_props)
    run(
        [
            str(msbuild),
            str(source / "PCbuild" / "pcbuild.proj"),
            "/t:Build",
            "/m:2",
            "/nr:false",
            "/nologo",
            "/v:minimal",
            "/p:Configuration=Release",
            "/p:Platform=x64",
            "/p:PlatformToolset=v145",
            "/p:IncludeExternals=true",
            "/p:IncludeTkinter=false",
            "/p:IncludeTests=false",
            "/p:IncludeTest=false",
            "/p:KillPython=false",
            f"/p:ExternalProps={props}",
            f"/p:ForceImportBeforeCppTargets={compile_props}",
            f"/p:PythonForBuild={sys.executable}",
        ],
        log=log,
        env=environment,
    )
    layout = work / "embed"
    # PC/layout consumes the combined license produced by python.vcxproj.
    # Missing licensing is a build failure, never replaced by a partial notice.
    if not (build_dir / "LICENSE.txt").is_file():
        raise FileNotFoundError("upstream PCbuild did not generate LICENSE.txt")
    run(
        [
            str(build_dir / "python.exe"),
            str(source / "PC" / "layout"),
            "--preset-embed",
            "--source",
            str(source),
            "--build",
            str(build_dir),
            "--arch",
            "amd64",
            "--copy",
            str(layout),
            "--temp",
            str(work / "layout-temp"),
        ],
        log=log,
        env=environment,
    )
    smoke = (
        "import sys,ssl,sqlite3,zlib,ctypes,bz2,lzma,html.parser,json,pyexpat,_elementtree;"
        "import xml.etree.ElementTree as ET;"
        "assert sys.version_info[:3]==(3,12,16);"
        "assert ssl.OPENSSL_VERSION.startswith('OpenSSL 3.5.9');"
        "assert sqlite3.sqlite_version=='3.54.1';assert zlib.ZLIB_RUNTIME_VERSION=='1.3.2';"
        "assert pyexpat.EXPAT_VERSION=='expat_2.10.0';"
        "data=b'CivicCast runtime check'*100;"
        "assert bz2.decompress(bz2.compress(data))==data;"
        "assert lzma.decompress(lzma.compress(data))==data;"
        "assert zlib.decompress(zlib.compress(data))==data;"
        "assert sqlite3.connect(':memory:').execute('select 42').fetchone()==(42,);"
        "html.parser.HTMLParser().feed('<p>agenda</p>');"
        "seen=[];parser=pyexpat.ParserCreate(namespace_separator='}');"
        "parser.StartElementHandler=lambda name,attrs: seen.append(name);"
        "q=chr(34);parser.Parse(('<r xmlns=' + q + 'urn:civiccast' + q + '><x>a&amp;b</x></r>').encode(), True);"
        "assert 'urn:civiccast}r' in seen and 'urn:civiccast}x' in seen;"
        "root=ET.fromstring('<r xmlns=' + q + 'urn:civiccast' + q + '><x>a&amp;b</x></r>');"
        "assert root.tag=='{urn:civiccast}r' and root[0].text=='a&b';"
        "print(json.dumps({'python':sys.version,'openssl':ssl.OPENSSL_VERSION,"
        "'sqlite':sqlite3.sqlite_version,'zlib':zlib.ZLIB_RUNTIME_VERSION,'expat':pyexpat.EXPAT_VERSION}))"
    )
    run([str(layout / "python.exe"), "-I", "-c", smoke], log=log, env=environment)
    licenses = layout / "runtime-licenses"
    licenses.mkdir(exist_ok=True)
    for name, relative in {
        "bzip2": "LICENSE",
        "libffi": "LICENSE",
        "openssl": "amd64/LICENSE.txt",
        "zlib": "LICENSE",
        "xz": "COPYING",
        "cpython": "LICENSE",
        "expat": "COPYING",
    }.items():
        shutil.copy2(paths[name] / relative, licenses / f"{name}.txt")
    shutil.copy2(source / "PC" / "crtlicense.txt", licenses / "microsoft-crt.txt")
    mpdecimal_header = (source / "Modules" / "_decimal" / "libmpdec" / "mpdecimal.h").read_text()
    (licenses / "mpdecimal.txt").write_text(
        mpdecimal_header.split("*/", 1)[0] + "*/\n", encoding="utf-8"
    )
    shutil.copy2(paths["xz"] / "COPYING.0BSD", licenses / "xz-0BSD.txt")
    (licenses / "sqlite.txt").write_text(
        "SQLite is in the public domain. https://sqlite.org/copyright.html\n\n"
        + "\n".join((paths["sqlite"] / "sqlite3.c").read_text(encoding="utf-8").splitlines()[:25]),
        encoding="utf-8",
    )
    provenance = {
        "schema_version": 1,
        "distribution": "cpython-embeddable-source-build",
        "version": VERSION,
        "architecture": "amd64",
        "inputs": inputs,
        "source_bundled_components": STATIC_COMPONENTS,
        "expat_vendored": {
            "input": EXPAT_SOURCE,
            "source_archive_sha256": sha256(expat_archive),
            "cpython_bundled_original": bundled_original_hashes,
            "local_customizations": local_hashes,
            "derived": vendored_expat,
        },
        "build": "official PCbuild Release x64 /m:2; official PC/layout --preset-embed",
        "native_dependency_overrides": "dependencies.props; xz upstream CMake static liblzma",
        "msbuild_sha256": sha256(msbuild),
        "cmake_sha256": sha256(Path(cmake)),
        "build_host": {"version": sys.version, "sha256": sha256(Path(sys.executable))},
        "builder_sha256": BUILDER_SHA256,
        "files": {
            file.relative_to(layout).as_posix(): {
                "sha256": sha256(file),
                "bytes": file.stat().st_size,
            }
            for file in sorted(layout.rglob("*"))
            if file.is_file() and file.name != "python-runtime-provenance.json"
        },
    }
    (layout / "python-runtime-provenance.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_suffix(output.suffix + ".partial")
    try:
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as bundle:
            for file in sorted(layout.rglob("*")):
                if file.is_file():
                    bundle.write(file, file.relative_to(layout).as_posix())
        partial.replace(output)
    finally:
        partial.unlink(missing_ok=True)
    report = {
        "version": VERSION,
        "path": str(output),
        "sha256": sha256(output),
        "bytes": output.stat().st_size,
        "provenance": provenance,
        "log": str(log),
    }
    output.with_suffix(".provenance.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--visual-studio", required=True, type=Path)
    parser.add_argument("--cmake", default=shutil.which("cmake"))
    args = parser.parse_args()
    if not args.cmake:
        parser.error("CMake 3.20+ is required to build current liblzma")
    result = build(
        work=args.work_dir,
        output=args.output,
        visual_studio=args.visual_studio,
        cmake=str(Path(args.cmake).resolve()),
    )
    print(
        json.dumps({key: value for key, value in result.items() if key != "provenance"}, indent=2)
    )


if __name__ == "__main__":
    main()
