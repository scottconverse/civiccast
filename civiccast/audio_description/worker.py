# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Isolated offline model process; never patches sockets in the control plane."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

from civiccast.audio_description.pipeline import build, digest, offline_sockets, save


def main() -> None:
    request_path = Path(sys.argv[1]).resolve()
    request = json.loads(request_path.read_text(encoding="utf-8"))
    work = request_path.parent
    if (work / "CANCEL").exists():
        raise SystemExit(130)
    offline_sockets()
    # Normalize a separate derivative and apply exact trims. The source is read only.
    source = work / "prepared.mp4"
    prepared_receipt = work / "prepared.json"
    source_hash = digest(request["source"])
    if source.is_file():
        prior = (
            json.loads(prepared_receipt.read_text(encoding="utf-8"))
            if prepared_receipt.is_file()
            else {}
        )
        if prior.get("source_sha256") != source_hash or prior.get("prepared_sha256") != digest(
            source
        ):
            raise ValueError(
                "Prepared media or its source changed. Generate a new description job."
            )
    if not source.is_file():
        partial = work / "prepared.partial.mp4"
        command = [
            (shutil.which("ffmpeg") or "ffmpeg"),
            "-y",
            "-v",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-i",
            request["source"],
            "-ss",
            str(request["trim_in"]),
        ]
        if request["trim_out"] is not None:
            duration = request["trim_out"] - request["trim_in"]
            if duration <= 0:
                raise ValueError("Recording trim interval is empty")
            command += ["-t", str(duration)]
        command += [
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(partial),
        ]
        subprocess.run(command, check=True)  # noqa: S603 - internal argument list; no shell; media inputs are confined.
        partial.replace(source)
        if digest(request["source"]) != source_hash:
            raise ValueError("Source changed while preparing the recording")
        save(prepared_receipt, {"source_sha256": source_hash, "prepared_sha256": digest(source)})
    try:
        build(
            source, request["output"], request["model"], request["captions"], 0, request["endpoint"]
        )
    except InterruptedError as exc:
        raise SystemExit(130) from exc


if __name__ == "__main__":
    main()
