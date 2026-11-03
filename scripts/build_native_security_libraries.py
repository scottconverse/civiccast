# SPDX-License-Identifier: Apache-2.0
"""Build the small ABI-compatible security replacements, using pinned sources.

This is an input preparation tool, not an installer or a new runtime. The existing
pack builders consume the reviewed archive through native_security_overlay.
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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.build_native_pyav_wheel import (  # noqa: E402
    find_vcvarsall,
    load_pinned_msvc_environment,
)

SOURCES = {
    "zlib": {
        "version": "1.3.2.1",
        "directory": "zlib-6be4bcda3da1d28b18a081c884b141a88a7cb84d",
        "url": "https://codeload.github.com/madler/zlib/tar.gz/6be4bcda3da1d28b18a081c884b141a88a7cb84d",
        "sha256": "791d3769f92102b4b83a790971d400c8440e0cbe4823276319a1bdad32551a15",
    },
    "libpng": {
        "version": "1.6.57",
        "directory": "libpng-1.6.57",
        "url": "https://codeload.github.com/pnggroup/libpng/tar.gz/refs/tags/v1.6.57",
        "sha256": "4cbb7b0746edc1683c9581b373365e955133b7f1243f171b7d1535b4415dfedb",
    },
    "libxml2": {
        "version": "2.15.4",
        "directory": "libxml2-2.15.4",
        "url": "https://download.gnome.org/sources/libxml2/2.15/libxml2-2.15.4.tar.xz",
        "sha256": "98087fd181d9070724f3fbc65c7377db03038eb92bd882374daff44940138821",
    },
    "harfbuzz": {
        "version": "15.0.0",
        "directory": "harfbuzz-15.0.0",
        "url": "https://github.com/harfbuzz/harfbuzz/releases/download/15.0.0/harfbuzz-15.0.0.tar.xz",
        "sha256": "968536e9abd782f07f7954044c0c4020199fcdd39b41d221ec42d2a3be4a44df",
    },
    "sqlite": {
        "version": "3.54.1",
        "directory": "sqlite-amalgamation-3540100",
        "url": "https://sqlite.org/2026/sqlite-amalgamation-3540100.zip",
        "sha256": "0a9c8e34352dad46006aa82836d5383bff9dc6bc9ce5a60553c05aa30e6ad1f9",
    },
    "expat": {
        "version": "2.10.0",
        "directory": "expat-2.10.0",
        "url": "https://github.com/libexpat/libexpat/releases/download/R_2_10_0/expat-2.10.0.tar.gz",
        "sha256": "5466a341f9c692ef7a958a6881311be597f8e1ce345984cf78414f54793dea4c",
    },
}


def package(work: Path) -> Path:
    """Emit a reviewable archive/lock; never replace committed pins implicitly."""
    from scripts.native_security_overlay import ARCHIVE_NAME, verify_expat_ordinal_contract

    prefix = work / "prefix/bin"
    verify_expat_ordinal_contract(prefix / "libexpat.dll")
    # CPython's SQLite DLL has SQLITE_OMIT_AUTOINIT and is NOT interchangeable.
    # Require a fresh-process open without sqlite3_initialize before packaging.
    subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import ctypes as c,sys; d=c.CDLL(sys.argv[1]); "
            "p=c.c_void_p(); d.sqlite3_open.argtypes=[c.c_char_p,c.POINTER(c.c_void_p)]; "
            "d.sqlite3_close.argtypes=[c.c_void_p]; "
            "assert d.sqlite3_open(b':memory:',c.byref(p))==0; assert d.sqlite3_close(p)==0",
            str(prefix / "sqlite3-0.dll"),
        ],
        check=True,
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    components = {
        name: {"version": source["version"], "source": source} for name, source in SOURCES.items()
    }
    components["security-libraries-build"] = {
        "version": "1",
        "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    files = {}
    blobs = {}
    microsoft = " AND LicenseRef-Microsoft-VCRedist"

    def add(name: str, source: Path, component: str, license_: str, **destinations: str) -> None:
        data = source.read_bytes()
        blobs[name] = data
        files[name] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "component": component,
            "license": license_,
            **destinations,
        }

    add(
        "z-1.dll",
        prefix / "z-1.dll",
        "zlib",
        "Zlib" + microsoft,
        gstreamer="bin/z-1.dll",
        postgres="bin/zlib1.dll",
    )
    add(
        "png16.dll", prefix / "png16.dll", "libpng", "Libpng" + microsoft, gstreamer="bin/png16.dll"
    )
    add(
        "libxml2.dll",
        prefix / "libxml2.dll",
        "libxml2",
        "MIT" + microsoft,
        postgres="bin/libxml2.dll",
    )
    add(
        "harfbuzz.dll",
        prefix / "harfbuzz.dll",
        "harfbuzz",
        "MIT" + microsoft,
        gstreamer="bin/harfbuzz.dll",
    )
    add(
        "sqlite3-0.dll",
        prefix / "sqlite3-0.dll",
        "sqlite",
        "blessing" + microsoft,
        gstreamer="bin/sqlite3-0.dll",
    )
    add(
        "libexpat.dll",
        prefix / "libexpat.dll",
        "expat",
        "MIT" + microsoft,
        gstreamer="bin/libexpat.dll",
    )
    for name, license_file, license_ in (
        ("zlib", "LICENSE", "Zlib"),
        ("libpng", "LICENSE", "Libpng"),
        ("libxml2", "Copyright", "MIT"),
        ("harfbuzz", "COPYING", "MIT"),
        ("expat", "COPYING", "MIT"),
    ):
        filename = f"{name}-LICENSE.txt"
        destinations = dict.fromkeys(
            ("gstreamer", "postgres")
            if name == "zlib"
            else ("postgres",)
            if name == "libxml2"
            else ("gstreamer",),
            f"licenses/security-libs/{filename}",
        )
        add(
            filename,
            work / SOURCES[name]["directory"] / license_file,
            name,
            license_,
            **destinations,
        )
    add(
        "sqlite-LICENSE.txt",
        ROOT / "civiccast/native/license_texts/blessing.txt",
        "sqlite",
        "blessing",
        gstreamer="licenses/security-libs/sqlite-LICENSE.txt",
    )
    add(
        "LicenseRef-Microsoft-VCRedist.txt",
        ROOT / "civiccast/native/license_texts/LicenseRef-Microsoft-VCRedist.txt",
        "security-libraries-build",
        "LicenseRef-Microsoft-VCRedist",
        gstreamer="licenses/security-libs/LicenseRef-Microsoft-VCRedist.txt",
        postgres="licenses/security-libs/LicenseRef-Microsoft-VCRedist.txt",
    )
    builder_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    provenance = {
        "sources": components,
        "builder_sha256": builder_sha256,
        "packager_sha256": builder_sha256,
        "build_log_sha256": hashlib.sha256((work / "build.log").read_bytes()).hexdigest(),
        "cmake_cache_sha256": {
            name: hashlib.sha256((work / f"build-{name}/CMakeCache.txt").read_bytes()).hexdigest()
            for name in SOURCES
        },
        "static_crt": ["zlib", "libpng", "libxml2", "harfbuzz", "sqlite", "expat"],
        "sqlite_automatic_initialization": "enabled; fresh-process sqlite3_open verified",
    }
    reused_components = work / "reused-components.json"
    if reused_components.exists():
        provenance["reused_components"] = json.loads(reused_components.read_text(encoding="utf-8"))
    proof = work / "security-libraries-provenance.json"
    proof.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    add(
        "provenance.json",
        proof,
        "security-libraries-build",
        "Apache-2.0",
        gstreamer="licenses/security-libs/provenance.json",
        postgres="licenses/security-libs/provenance.json",
    )
    archive = work / ARCHIVE_NAME
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for name, data in sorted(blobs.items()):
            info = zipfile.ZipInfo(name, (2026, 10, 29, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, data)
    lock = {
        "schema_version": 1,
        "components": components,
        "files": files,
        "archive": {
            "filename": ARCHIVE_NAME,
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "bytes": archive.stat().st_size,
        },
    }
    (work / "native-windows-security-libraries.lock.json").write_text(
        json.dumps(lock, indent=2) + "\n", encoding="utf-8"
    )
    return archive


def build(work: Path, sdk: Path, iconv_library: Path, only: list[str] | None = None) -> None:
    work = work.resolve()
    work.mkdir(parents=True, exist_ok=True)

    def hidden_run(*args, **kwargs):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        return subprocess.run(*args, **kwargs)

    env = load_pinned_msvc_environment(find_vcvarsall(runner=hidden_run), runner=hidden_run)
    tools = Path(
        "C:/Program Files (x86)/Microsoft Visual Studio/18/BuildTools/Common7/IDE/CommonExtensions/Microsoft/CMake"
    )
    cmake = shutil.which("cmake") or str(tools / "CMake/bin/cmake.exe")
    ninja = shutil.which("ninja") or str(tools / "Ninja/ninja.exe")
    commands = []

    def run(args: list[str]) -> None:
        commands.append(args)
        with (work / "build.log").open("a", encoding="utf-8") as log:
            log.write(json.dumps(args) + "\n")
            log.flush()
            subprocess.run(
                args,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )

    for name, spec in SOURCES.items():
        if only and name not in only:
            continue
        archive = work / f"{name}.tar"
        if not archive.exists():
            urllib.request.urlretrieve(spec["url"], archive)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != spec["sha256"]:
            raise ValueError(f"unverified source: {name}")
        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as bundle:
                for member in bundle.infolist():
                    path = work / member.filename
                    if (
                        not path.resolve().is_relative_to(work)
                        or (member.external_attr >> 16) & 0o170000 == 0o120000
                    ):
                        raise ValueError("unsafe source archive member")
                    if member.is_dir():
                        continue
                    data = bundle.read(member)
                    if path.exists() and path.read_bytes() != data:
                        raise ValueError(f"modified source cache: {path}")
                    if not path.exists():
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(data)
            continue
        # Verify cached source before incremental reuse; CMake does not change
        # upstream source files in these out-of-tree configurations.
        with tarfile.open(archive) as bundle:
            for member in bundle:
                # Upstream documentation alias, not a compilation input.
                if (
                    name == "harfbuzz"
                    and member.name == spec["directory"] + "/CLAUDE.md"
                    and member.issym()
                    and member.linkname == "AGENTS.md"
                ):
                    continue
                path = work / member.name
                if not path.resolve().is_relative_to(work) or member.issym() or member.islnk():
                    raise ValueError("unsafe source archive member")
                if not member.isfile():
                    continue
                stream = bundle.extractfile(member)
                assert stream is not None
                data = stream.read()
                if path.exists() and path.read_bytes() != data:
                    raise ValueError(f"modified source cache: {path}")
                if not path.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)

    prefix = work / "prefix"
    for name, spec in SOURCES.items():
        if only and name not in only:
            continue
        wrapper = work / f"wrapper-{name}"
        wrapper.mkdir(exist_ok=True)
        source = work / spec["directory"]
        options: list[str] = []
        after = ""
        if name == "zlib":
            options = ["-DZLIB_BUILD_TESTING=OFF", "-DZLIB_BUILD_STATIC=OFF"]
            after = 'set_target_properties(zlib PROPERTIES OUTPUT_NAME "z-1")'
        elif name == "libpng":
            options = [
                "-DPNG_STATIC=OFF",
                "-DPNG_TESTS=OFF",
                "-DPNG_TOOLS=OFF",
                f"-DZLIB_LIBRARY={prefix.as_posix()}/lib/z-1.lib",
                f"-DZLIB_INCLUDE_DIR={prefix.as_posix()}/include",
            ]
            after = 'set_target_properties(png_shared PROPERTIES OUTPUT_NAME "png16")'
        elif name == "libxml2":
            options = [
                "-DLIBXML2_WITH_PROGRAMS=OFF",
                "-DLIBXML2_WITH_TESTS=OFF",
                "-DLIBXML2_WITH_ICONV=ON",
                f"-DIconv_LIBRARY={iconv_library.as_posix()}",
                f"-DIconv_INCLUDE_DIR={(iconv_library.parent.parent / 'include').as_posix()}",
            ]
            after = 'set_target_properties(LibXml2 PROPERTIES PREFIX "" OUTPUT_NAME "libxml2")'
        elif name == "harfbuzz":
            options = [
                "-DHB_HAVE_FREETYPE=ON",
                "-DHB_BUILD_UTILS=OFF",
                "-DHB_BUILD_SUBSET=OFF",
                "-DHB_BUILD_RASTER=OFF",
                "-DHB_BUILD_VECTOR=OFF",
                "-DHB_BUILD_GPU=OFF",
                f"-DFREETYPE_LIBRARY_RELEASE={sdk.as_posix()}/lib/freetype.lib",
                f"-DFREETYPE_INCLUDE_DIR_ft2build={sdk.as_posix()}/include/freetype2",
                f"-DFREETYPE_INCLUDE_DIR_freetype2={sdk.as_posix()}/include/freetype2",
            ]
        elif name == "expat":
            options = [
                "-DEXPAT_BUILD_TOOLS=OFF",
                "-DEXPAT_BUILD_EXAMPLES=OFF",
                "-DEXPAT_BUILD_TESTS=OFF",
                "-DEXPAT_BUILD_DOCS=OFF",
                "-DEXPAT_BUILD_PKGCONFIG=OFF",
                "-DEXPAT_SHARED_LIBS=ON",
                "-DEXPAT_ENABLE_INSTALL=ON",
                "-DEXPAT_CHAR_TYPE=char",
                "-DEXPAT_DTD=ON",
                "-DEXPAT_GE=ON",
                "-DEXPAT_NS=ON",
                "-DEXPAT_MSVC_STATIC_CRT=ON",
            ]
        body = f'add_subdirectory("{source.as_posix()}" upstream)\n{after}\n'
        if name == "sqlite":
            body = (
                f'add_library(sqlite3 SHARED "{source.as_posix()}/sqlite3.c")\n'
                'target_compile_definitions(sqlite3 PRIVATE "SQLITE_API=__declspec(dllexport)" '
                "SQLITE_THREADSAFE=1 SQLITE_ENABLE_FTS4 SQLITE_ENABLE_FTS5 SQLITE_ENABLE_RTREE "
                "SQLITE_ENABLE_MATH_FUNCTIONS SQLITE_ENABLE_COLUMN_METADATA)\n"
                'set_target_properties(sqlite3 PROPERTIES OUTPUT_NAME "sqlite3-0")\n'
                "install(TARGETS sqlite3 RUNTIME DESTINATION bin LIBRARY DESTINATION lib ARCHIVE DESTINATION lib)\n"
            )
        (wrapper / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.18)\nproject(SecurityDependency LANGUAGES C CXX)\n"
            + body,
            encoding="utf-8",
        )
        target = work / f"build-{name}"
        run(
            [
                cmake,
                "-S",
                str(wrapper),
                "-B",
                str(target),
                "-G",
                "Ninja",
                f"-DCMAKE_MAKE_PROGRAM={ninja}",
                "-DCMAKE_BUILD_TYPE=Release",
                "-DBUILD_SHARED_LIBS=ON",
                "-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded",
                "-DCMAKE_POLICY_DEFAULT_CMP0091=NEW",
                f"-DCMAKE_INSTALL_PREFIX={prefix.as_posix()}",
                *options,
            ]
        )
        run([cmake, "--build", str(target), "--parallel", "2"])
        run([cmake, "--install", str(target)])
        print(f"Built {name} {spec['version']}", flush=True)
    (work / "build-provenance.json").write_text(
        json.dumps(
            {
                "sources": SOURCES,
                "commands": commands,
                "builder_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--iconv-library", type=Path, required=True)
    parser.add_argument("--only", nargs="+", choices=tuple(SOURCES))
    parser.add_argument("--package-only", action="store_true")
    args = parser.parse_args()
    if args.package_only:
        print(package(args.work))
    else:
        build(args.work, args.sdk, args.iconv_library, args.only)
