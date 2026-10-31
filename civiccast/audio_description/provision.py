# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Explicit narrator provisioning; processing never downloads model assets."""

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path

REPO = "kyutai/pocket-tts-without-voice-cloning"
REV = "a7056f505a4a8ca72d8b5fb1440071d6f1a35739"
FILES = {
    "model.safetensors": (
        REV,
        "languages/english/model.safetensors",
        "4c56cbd171c63e8a26ea61f5d48b8a0cd8f3c51f4652c0ab9a19bfcced8c931d",
    ),
    "tokenizer.json": (
        "00eac05ed3d16bdc3f6b5d598874019c34a89214",
        "languages/english/tokenizer.json",
        "f498428e1eafee50492f7be13dc9bfafcfc12e508cd0eb1b01c92ecd5d8c6687",
    ),
    "alba.safetensors": (
        REV,
        "languages/english/embeddings/alba.safetensors",
        "5850f417028fb2bf6de31e5e395e56ee7a21ac0e52ef31aebd4a6a2847d72e9c",
    ),
}


def provision(destination: Path) -> None:
    import pocket_tts  # type: ignore[import-untyped]
    import yaml  # type: ignore[import-untyped]

    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    for name, (revision, remote, expected) in FILES.items():
        target = destination / name
        if not target.is_file():
            partial = target.with_suffix(".partial")
            url = f"https://huggingface.co/{REPO}/resolve/{revision}/{remote}"
            with urllib.request.urlopen(url, timeout=120) as response, partial.open("wb") as output:  # noqa: S310 - fixed HTTPS source
                while data := response.read(4 * 1024 * 1024):
                    output.write(data)
            with partial.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                    raise ValueError(f"Narrator download failed verification: {name}")
            partial.replace(target)
        with target.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                raise ValueError(f"Existing narrator asset failed verification: {name}")
    from civiccast.audio_description.sound import MODEL_SHA256, MODEL_URL

    sound = destination / "yamnet.onnx"
    if not sound.is_file():
        partial = sound.with_suffix(".partial")
        with (
            urllib.request.urlopen(MODEL_URL, timeout=120) as response,  # noqa: S310 - fixed pinned HTTPS model URL
            partial.open("wb") as output,
        ):
            while data := response.read(4 * 1024 * 1024):
                output.write(data)
        with partial.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != MODEL_SHA256:
                raise ValueError("Speech classifier download failed verification")
        partial.replace(sound)
    with sound.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != MODEL_SHA256:
            raise ValueError("Existing speech classifier hash mismatch")
    (destination / "YAMNET-NOTICE.txt").write_text(
        "Google YAMNet (AudioSet), Apache License 2.0.\n"
        "Source: https://github.com/tensorflow/models/tree/master/research/audioset/yamnet\n"
        "License: https://www.apache.org/licenses/LICENSE-2.0\n"
        f"Pinned ONNX conversion: {MODEL_URL}\nSHA-256: {MODEL_SHA256}\n",
        encoding="utf-8",
    )
    config = yaml.safe_load(
        (Path(pocket_tts.__file__).parent / "config/english.yaml").read_text(encoding="utf-8")
    )
    config["weights_path"] = str(destination / "model.safetensors")
    config.pop("weights_path_without_voice_cloning", None)
    config["flow_lm"]["lookup_table"]["tokenizer_path"] = str(destination / "tokenizer.json")
    (destination / "local.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    (destination / "NOTICE.txt").write_text(
        "Pocket TTS by Kyutai: MIT code; non-cloning weights CC BY 4.0.\n"
        "Alba MacKenna fixed voice: CC BY 4.0. Synthetic narration, not a recording of Alba making these statements.\n"
        f"https://huggingface.co/{REPO}/tree/{REV}\n"
        "https://huggingface.co/kyutai/tts-voices\n",
        encoding="utf-8",
    )
    (destination / "provision.json").write_text(json.dumps(FILES, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    provision(parser.parse_args().directory)
