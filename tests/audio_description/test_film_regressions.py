# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Regressions for observed film failures, not a model-quality simulation."""

from itertools import pairwise
from types import SimpleNamespace

import pytest
from PIL import Image

from civiccast.audio_description.pipeline import Vision, consolidate, protections
from civiccast.audio_description.sound import model_path, protected_frames
from civiccast.audio_description.visual import extract, sample_points, similar


def test_collision_between_cut_and_periodic_sample_is_one_event():
    points = sample_points([7.79, 7.83, 15.5], 24)
    assert any(7.9 < p < 8.1 for p in points)
    assert all(b - a >= 2 for a, b in pairwise(points))


def test_duplicate_description_with_different_evidence_is_consolidated():
    fact = {"text": "A hand reaches toward a control panel.", "kind": "visual_observation"}
    events = [
        {"time": t, "until": t + 4, "visual": {"facts": [{**fact, "quote": quote}]}}
        for t, quote in [(0, "hand by panel"), (4, "panel with a hand")]
    ]
    merged = consolidate(events)
    assert len(merged) == 1
    assert merged[0]["until"] == 8


def test_deduplication_preserves_changed_amount_action_and_negation():
    def fact(text, kind="visual_observation"):
        return {"text": text, "quote": text, "kind": kind}

    assert not similar(
        fact("The budget is $2 million.", "printed_text"),
        fact("The budget is $3 million.", "printed_text"),
    )
    assert not similar(fact("A rabbit holds an apple."), fact("A rabbit drops an apple."))
    assert not similar(fact("The door is open."), fact("The door is not open."))


def test_black_screen_generates_no_narration_or_model_request(tmp_path):
    frame = tmp_path / "black.png"
    Image.new("RGB", (64, 64), "black").save(frame)
    vision = SimpleNamespace(generate=lambda *a: pytest.fail("black frame must be omitted"))
    assert extract(vision, frame, None, None, [])["facts"] == []


def test_model_stays_warm_between_requests(tmp_path):
    vision = object.__new__(Vision)
    vision.model = "local-vision"
    vision.trace_root = tmp_path / "traces"
    vision.runtime_version = "test"
    vision.row = {"digest": "one"}
    vision.cache_hits = 0
    requests = []

    def request(path, body):
        requests.append(body)
        return {"done": True, "done_reason": "stop", "response": '{"facts":[]}'}

    vision.request = request
    vision.generate("first distinct prompt")
    vision.generate("second distinct prompt")
    assert [r["keep_alive"] for r in requests] == ["5m", "5m"]


def test_music_does_not_veto_speech_but_music_alone_is_not_speech():
    scores = [0.0] * 521
    scores[132] = 0.95
    assert not protected_frames(scores)
    scores[0] = 0.2
    assert protected_frames(scores)


def test_music_gaps_retain_caption_and_detected_speech_protection(monkeypatch, tmp_path):
    import civiccast.audio_description.pipeline as pipeline
    import civiccast.audio_description.sound as sound

    monkeypatch.setattr(pipeline.ffmpeg, "probe_video", lambda _: SimpleNamespace(has_audio=True))
    monkeypatch.setattr(sound, "speech_spans", lambda *_: ([[5.0, 7.0]], []))
    result = protections(tmp_path / "film.mp4", 20, [{"start": 10.0, "end": 12.0}])
    gaps = result["safe_gaps"]
    assert any(b - a > 4 for a, b in gaps)
    for a, b in gaps:
        for x, y in result["protected_spans"]:
            assert min(b, y) <= max(a, x)
    assert [9.7, 12.3] in result["protected_spans"]


def test_processing_rejects_unverified_sound_model(tmp_path, monkeypatch):
    monkeypatch.setenv("CIVICCAST_AD_SOUND_DIR", str(tmp_path))
    (tmp_path / "yamnet.onnx").write_bytes(b"not the pinned model")
    with pytest.raises(ValueError, match="hash mismatch"):
        model_path()


def test_near_black_fading_title_is_not_guessed(tmp_path):
    frame = tmp_path / "fading-title.png"
    image = Image.new("RGB", (64, 64), (15, 15, 15))
    image.putpixel((32, 32), (80, 80, 80))
    image.save(frame)
    vision = SimpleNamespace(generate=lambda *a: pytest.fail("low-contrast frame must be omitted"))
    result = extract(vision, frame, None, None, [])
    assert result["facts"] == []
    assert result["uncertain_details"]


def test_both_visual_passes_see_only_target_not_neighbor_scenes(tmp_path):
    target = tmp_path / "target.png"
    Image.new("RGB", (64, 64), "white").save(target)
    calls = []

    def generate(prompt, images, schema):
        calls.append(images)
        return {
            "facts": [{"text": "A person walks.", "quote": "person", "kind": "visual_observation"}],
            "uncertain_details": [],
            "unchanged": False,
        }

    before, after = tmp_path / "before.png", tmp_path / "after.png"
    extract(SimpleNamespace(generate=generate), target, before, after, [])
    assert calls[0] == target
    assert calls[1] == target


def test_obscured_printed_quote_is_omitted_not_spoken(tmp_path):
    image = tmp_path / "frame.png"
    Image.new("RGB", (64, 64), "white").save(image)
    answer = {
        "facts": [
            {
                "text": "The headline says ROBOT REVOLUTION.",
                "quote": "ROBOT REVOLUTION",
                "kind": "printed_text",
            }
        ],
        "uncertain_details": ["The full text of the newspaper article is partially obscured."],
        "unchanged": False,
    }
    result = extract(SimpleNamespace(generate=lambda *a: answer), image, None, None, [])
    assert result["facts"] == []
    assert any("ROBOT REVOLUTION" in s for s in result["uncertain_details"])


def test_clear_printed_value_survives_unrelated_visual_uncertainty(tmp_path):
    image = tmp_path / "frame.png"
    Image.new("RGB", (64, 64), "white").save(image)
    fact = {"text": "The budget is $2 million.", "quote": "$2 million", "kind": "printed_text"}
    answer = {
        "facts": [fact],
        "uncertain_details": ["The room's location is unclear."],
        "unchanged": False,
    }
    result = extract(SimpleNamespace(generate=lambda *a: answer), image, None, None, [])
    assert result["facts"] == [fact]


def test_conflicting_object_labels_are_omitted_and_context_does_not_anchor_first_read(tmp_path):
    image = tmp_path / "frame.png"
    Image.new("RGB", (64, 64), "white").save(image)
    calls = []
    answers = iter(
        [
            {
                "facts": [
                    {
                        "text": "A woman crouches beside a discarded helmet.",
                        "quote": "helmet",
                        "kind": "visual_observation",
                    }
                ],
                "uncertain_details": [],
            },
            {
                "facts": [
                    {
                        "text": "A girl crouches beside a wounded creature.",
                        "quote": "creature",
                        "kind": "visual_observation",
                    }
                ],
                "uncertain_details": [],
            },
        ]
    )

    def generate(prompt, *args):
        calls.append(prompt)
        return next(answers)

    result = extract(
        SimpleNamespace(generate=generate), image, None, None, ["A winged creature rests."]
    )
    assert result["facts"] == []
    assert any("disagree" in s.lower() for s in result["uncertain_details"])
    assert "A winged creature rests." not in calls[0]
    assert "A winged creature rests." in calls[1]
    assert "discarded helmet" not in calls[1]


def test_one_changed_species_is_not_hidden_by_other_matching_words(tmp_path):
    image = tmp_path / "frame.png"
    Image.new("RGB", (64, 64), "white").save(image)
    answers = iter(
        [
            {
                "facts": [
                    {
                        "text": "A white cat sits beside a tree.",
                        "quote": "cat",
                        "kind": "visual_observation",
                    }
                ],
                "uncertain_details": [],
            },
            {
                "facts": [
                    {
                        "text": "A white rabbit sits beside a tree.",
                        "quote": "rabbit",
                        "kind": "visual_observation",
                    }
                ],
                "uncertain_details": [],
            },
        ]
    )
    result = extract(SimpleNamespace(generate=lambda *a: next(answers)), image, None, None, [])
    assert result["facts"] == []


def test_agreement_keeps_only_the_shared_broader_fact(tmp_path):
    image = tmp_path / "frame.png"
    Image.new("RGB", (64, 64), "white").save(image)
    texts = ["A rabbit holds an apple.", "A white rabbit holds an apple."]
    answers = iter(
        [
            {
                "facts": [
                    {"text": text, "quote": "rabbit with apple", "kind": "visual_observation"}
                ],
                "uncertain_details": [],
            }
            for text in texts
        ]
    )
    result = extract(SimpleNamespace(generate=lambda *a: next(answers)), image, None, None, [])
    assert [f["text"] for f in result["facts"]] == [texts[0]]


@pytest.mark.parametrize("quote", ["\u20ac2 million", "2 million", "$2% million"])
def test_print_agreement_does_not_erase_currency_or_percent_symbols(quote):
    from civiccast.audio_description.visual import agreed_fact

    left = {"text": "The budget is two million.", "quote": "$2 million", "kind": "printed_text"}
    assert agreed_fact(left, {**left, "quote": quote}) is None


def test_shortening_keeps_newspaper_attribution_instead_of_announcing_a_robot():
    from civiccast.audio_description.pipeline import extractive_shortening

    original = "A hand holds a newspaper with a headline about a robot."
    assert extractive_shortening(original, "a robot") is None
    assert extractive_shortening(original, "a headline about a robot") == "a headline about a robot"


def test_uncertain_stone_like_label_is_broadened_without_rewriting_raw_evidence(tmp_path):
    image = tmp_path / "frame.png"
    Image.new("RGB", (64, 64), "white").save(image)
    original = "A large, stone-like creature rests inside a grassy burrow."
    fact = {"text": original, "quote": "creature in burrow", "kind": "visual_observation"}
    answer = {"facts": [fact], "uncertain_details": [], "unchanged": False}
    result = extract(SimpleNamespace(generate=lambda *a: answer), image, None, None, [])
    assert result["facts"][0]["text"] == "A large creature rests inside a grassy burrow."
    assert result["draft"]["facts"][0]["text"] == original
    assert any("stone-like" in s for s in result["uncertain_details"])
