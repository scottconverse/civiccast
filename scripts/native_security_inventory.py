# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Export the shipped native release inputs as a CycloneDX scanner inventory.

Unlike uv.lock this includes the Windows-only wheel locks, embedded CPython,
native server/media artifacts and the static offline OCR dependency closure.
Artifact versions come from the build's authoritative pins, never a second list.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from civiccast.native.app_payload import INTERPRETER_VERSION  # noqa: E402
from scripts.build_native_pyav_wheel import ffmpeg_provenance  # noqa: E402


def inventory() -> dict:
    components = []

    def add(
        name: str,
        version: str,
        *,
        cpe: str | None = None,
        cpe_version: str | None = None,
        purl: str | None = None,
        source: str,
        digest: str | None = None,
    ) -> None:
        item = {
            "type": "library",
            "name": name,
            "version": version,
            "bom-ref": f"{name}@{version}",
            "properties": [{"name": "civiccast:pin-source", "value": source}],
        }
        if cpe:
            item["cpe"] = f"cpe:2.3:a:{cpe}:{cpe_version or version}:*:*:*:*:*:*:*"
        if cpe_version:
            item["properties"].append(
                {"name": "civiccast:cpe-baseline-version", "value": cpe_version}
            )
        if purl:
            item["purl"] = purl
        if digest:
            item["hashes"] = [{"alg": "SHA-256", "content": digest}]
        components.append(item)

    for filename in ("requirements-native-app.txt", "requirements-native-runtime.txt"):
        for name, version in re.findall(
            r"^([\w.-]+)==([^\s\\]+)", (ROOT / filename).read_text(), re.MULTILINE
        ):
            add(name, version, purl=f"pkg:pypi/{name}@{version}", source=filename)
    add(
        "cpython",
        INTERPRETER_VERSION,
        cpe="python:python",
        source="civiccast/native/app_payload.py",
    )
    native = json.loads((ROOT / "native-windows-runtime-dependencies.lock.json").read_text())
    cpes = {
        "ffmpeg": "ffmpeg:ffmpeg",
        "node": "nodejs:node.js",
        "ollama": "ollama:ollama",
        "postgres": "postgresql:postgresql",
        "tsduck": "tsduck:tsduck",
    }
    for name, artifact in native["artifacts"].items():
        # The modern station core is a NOTICE-only placeholder. Node is a build
        # tool used by portal bundling, never a native installer runtime member.
        if name == "node":
            continue
        version = artifact["version"]
        cpe_version = None
        if name == "ffmpeg":
            release = re.search(r"\d+\.\d+\.\d+", version)
            if release:
                version = release.group()
            elif re.fullmatch(r"N-\d+-g[0-9a-f]+", version):
                # Retain exact git build identity. Scan conservatively against
                # the latest release baseline; fixed-backport triage is per ID.
                cpe_version = "9.0.2"
            else:
                raise ValueError(f"Unrecognized FFmpeg security inventory identity: {version}")
        elif name == "postgres":
            version = version.split("-")[0]
        elif name == "tsduck":
            version = version.replace("-", ".")
        add(
            name,
            version,
            cpe=cpes[name],
            cpe_version=cpe_version,
            source="native-windows-runtime-dependencies.lock.json",
            digest=artifact["sha256"],
        )
    ocr = json.loads((ROOT / "native-windows-ocr.lock.json").read_text())
    for name, artifact in ocr["components"].items():
        add(
            name,
            artifact["version"],
            cpe={
                "tesseract": "tesseract_project:tesseract",
                "leptonica": "leptonica:leptonica",
            }.get(name),
            source="native-windows-ocr.lock.json",
            digest=artifact["sha256"],
        )
    # GStreamer wheels embed the upstream native runtime; CPE catches its C CVEs.
    gst_version = next(item["version"] for item in components if item["name"] == "gstreamer-libs")
    add(
        "gstreamer",
        gst_version,
        cpe="gstreamer:gstreamer",
        source="requirements-native-runtime.txt",
    )
    pyav_ffmpeg = ffmpeg_provenance()
    add(
        "pyav-ffmpeg",
        str(pyav_ffmpeg["build"]),
        cpe="ffmpeg:ffmpeg",
        cpe_version="8.1.3",
        source="scripts/build_native_pyav_wheel.py",
        digest=str(pyav_ffmpeg["source_archive_sha256"]),
    )
    # gst-libav keeps a separate stable ABI and independently pinned source profile.
    gstreamer_ffmpeg = json.loads(
        (ROOT / "native-windows-gstreamer-ffmpeg.lock.json").read_text(encoding="utf-8")
    )
    add(
        "gstreamer-ffmpeg",
        gstreamer_ffmpeg["version"],
        cpe="ffmpeg:ffmpeg",
        cpe_version="7.1.5",
        source="native-windows-gstreamer-ffmpeg.lock.json",
        digest=gstreamer_ffmpeg["provenance"]["source_sha256"],
    )
    bundled = json.loads((ROOT / "security/native-bundled-components.json").read_text())
    for source in bundled["sources"]:
        if source["parent"].startswith("wheel:"):
            name = source["parent"].split(":", 1)[1]
            text = (ROOT / "requirements-native-app.txt").read_text()
            stanza = re.search(
                rf"^{re.escape(name)}==.*?(?=^\S|\Z)", text, re.MULTILINE | re.DOTALL
            )
            if (
                stanza is None
                or not stanza.group().startswith(f"{name}=={source['wheel_version']} ")
                or source["wheel_sha256"] not in stanza.group()
            ):
                raise ValueError(f"Stale bundled wheel inventory: {name}")
            actual = expected = source["wheel_sha256"]
        elif source["parent"] == "gstreamer":
            actual = hashlib.sha256(
                (ROOT / "requirements-native-runtime.txt").read_bytes()
            ).hexdigest()
            expected = source["requirements_sha256"]
        else:
            actual = native["artifacts"][source["parent"]]["sha256"]
            expected = source["archive_sha256"]
        if actual != expected:
            raise ValueError(
                f"Stale native DLL inventory for {source['parent']}; reconcile source pins"
            )
        for component in source["components"]:
            pin_source = "security/native-bundled-components.json"
            version = component["version"]
            digest = None
            if component.get("source_lock"):
                pin_source = component["source_lock"]
                replacement = json.loads((ROOT / pin_source).read_text())
                if component.get("source_component"):
                    replacement = replacement["components"][component["source_component"]]
                version = replacement["version"]
                digest = replacement.get("source", replacement)["sha256"]
            add(
                component["name"],
                version,
                cpe=component["cpe"],
                source=pin_source,
                digest=digest,
            )
            for field in ("upstream_revision", "version_status"):
                if component.get(field):
                    components[-1]["properties"].append(
                        {"name": f"civiccast:{field.replace('_', '-')}", "value": component[field]}
                    )
            if source.get("wheel_sha256"):
                components[-1]["properties"].append(
                    {"name": "civiccast:parent-wheel-sha256", "value": source["wheel_sha256"]}
                )

    # PCbuild dependency declarations are also the acquisition pins. Read literals
    # without running build code or duplicating versions in a scanner-only list.
    python_builder = ast.parse((ROOT / "scripts/build_native_python_runtime.py").read_text())
    literals = {
        node.targets[0].id: node.value
        for node in python_builder.body
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
    }
    if ast.literal_eval(literals["VERSION"]) == INTERPRETER_VERSION:
        cpes = {
            "bzip2": "bzip:bzip2",
            "libffi": "libffi:libffi",
            "openssl": "openssl:openssl",
            "sqlite": "sqlite:sqlite",
            "zlib": "zlib:zlib",
            "xz": "tukaani:xz",
        }
        for dependency in ast.literal_eval(literals["DEPENDENCIES"]):
            add(
                f"cpython-{dependency['name']}",
                dependency["version"],
                cpe=cpes[dependency["name"]],
                source="scripts/build_native_python_runtime.py",
                digest=dependency["sha256"],
            )
        for dependency in ast.literal_eval(literals["STATIC_COMPONENTS"]):
            add(
                f"cpython-{dependency['name']}",
                dependency["version"],
                cpe={"libexpat": "libexpat_project:libexpat", "mpdecimal": "bytereef:mpdecimal"}[
                    dependency["name"]
                ],
                source=dependency.get("source", "scripts/build_native_python_runtime.py"),
                digest=dependency.get("sha256"),
            )
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "components": sorted(components, key=lambda item: item["bom-ref"]),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.write_text(json.dumps(inventory(), indent=2) + "\n", encoding="utf-8")
