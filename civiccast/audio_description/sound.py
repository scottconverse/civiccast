# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Bounded, offline Google YAMNet speech protection (music is not dialogue)."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

MODEL_REVISION = "ac2ca3bd45d12ec1f19f1144205ea529b4e9dedf"
MODEL_SHA256 = "1510041dce24a2e9e84ec546807ac408ae496da6d1ed41bc3ccba649623f8e19"
MODEL_URL = f"https://huggingface.co/zeropointnine/yamnet-onnx/resolve/{MODEL_REVISION}/yamnet.onnx"


def model_path() -> Path:
    # Provision alongside the narrator; processing never fetches weights.
    root = Path(
        os.environ.get("CIVICCAST_AD_SOUND_DIR", os.environ.get("CIVICCAST_AD_VOICE_DIR", "."))
    )
    path = root / "yamnet.onnx"
    if not path.is_file():
        raise ValueError(
            "Local speech classifier missing. Provision audio-description assets first."
        )
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != MODEL_SHA256:
            raise ValueError(
                "Local speech classifier hash mismatch. Repair audio-description assets."
            )
    return path


def protected_frames(scores: Any) -> bool:
    # Fixed Google AudioSet map: speech/shouts/whispers 0:13; human
    # laughter/crying/singing 13:35. Protect speech even with music present.
    # Vocal effects have a separate higher threshold, not a music veto.
    return bool(max(scores[:13]) >= 0.10 or max(scores[13:35]) >= 0.30)


def speech_spans(source: Path, duration: float) -> tuple[list[list[float]], list[dict[str, Any]]]:
    import numpy as np
    import onnxruntime as ort  # type: ignore[import-untyped]

    from civiccast.audio_description.pipeline import await_background

    ort.disable_telemetry_events()
    options = ort.SessionOptions()
    options.intra_op_num_threads = 4
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(str(model_path()), options, providers=["CPUExecutionProvider"])
    decoder = subprocess.Popen(  # noqa: S603 - fixed argument list, confined source, no shell.
        [
            shutil.which("ffmpeg") or "ffmpeg",
            "-v",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-i",
            str(source),
            "-vn",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-f",
            "f32le",
            "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert decoder.stdout is not None
    spans: list[list[float]] = []
    trace: list[dict[str, Any]] = []
    # Consume 9.6 seconds per block with 0.96 seconds of lookahead, so the
    # 0.96-second classifier windows spanning a block boundary see real audio.
    block, lookahead, start, carry = 153600, 15360, 0.0, b""
    try:
        while True:
            raw = carry + decoder.stdout.read((block + lookahead) * 4 - len(carry))
            if not raw:
                break
            samples = np.frombuffer(raw, dtype="<f4")
            valid = len(samples)
            if valid < 15600:
                samples = np.pad(samples, (0, 15600 - valid))
            values = session.run(["output_0"], {"waveform": samples})[0]
            for index, scores in enumerate(values[:20]):
                point = start + index * 0.48
                if point >= duration or index * 7680 >= valid:
                    break
                speech = float(max(scores[:13]))
                vocal = float(max(scores[13:35]))
                trace.append(
                    {
                        "time": round(point, 4),
                        "speech": speech,
                        "vocal": vocal,
                        "music": float(scores[132]),
                    }
                )
                if protected_frames(scores):
                    a, b = max(0.0, point - 0.3), min(duration, point + 0.96 + 0.3)
                    if spans and a <= spans[-1][1]:
                        spans[-1][1] = max(spans[-1][1], b)
                    else:
                        spans.append([a, b])
            if valid <= block:
                break
            carry = raw[block * 4 :]
            start += 9.6
            # The owning worker checks its cancellation marker between stages;
            # this bounded loop also observes cancellation for long recordings.
            output_root = os.environ.get("CIVICCAST_AD_ACTIVE_OUTPUT")
            if output_root:
                await_background(Path(output_root))
        # Drain the last partial decoder output and require a clean decoder exit.
        decoder.stdout.read()
        if decoder.wait() != 0:
            raise RuntimeError("Speech classification audio decoding failed")
    finally:
        if decoder.poll() is None:
            decoder.kill()
            decoder.wait()
        decoder.stdout.close()
    return spans, trace
