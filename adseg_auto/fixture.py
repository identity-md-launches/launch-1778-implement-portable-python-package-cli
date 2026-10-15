"""Generate authored SYNTHETIC evidence and explicit replay, never real-set labels.

The authoring provider below uses this module's storyboard to record a fixture.
It is not an inference provider exposed by the segmentation CLI. Replay then
exercises the ordinary pipeline offline; it demonstrates contracts, not accuracy.
"""

import argparse
from pathlib import Path
import tempfile

from .pipeline import Config, run
from .util import digest, file_hash, write_json


STORY = [
    (-2, 2.4, "studio_with_integrated_sponsor", "program", (30, 80, 170)),
    (2.4, 4.1, "yunshan_ad_broadcast_1", "ad", (220, 20, 40)),
    (4.1, 5.3, "yunshan_ad_broadcast_2", "ad", (20, 210, 40)),
    (5.3, 5.64, "short_channel_promotion", "program", (210, 200, 20)),
    (5.64, 7.3, "independent_trailer_1", "program", (200, 30, 210)),
    (7.3, 9, "independent_trailer_2", "program", (20, 210, 210)),
    (9, 10.4, "public_service_ad", "ad", (240, 120, 20)),
    (10.4, 15, "shopping_program", "program", (80, 40, 210)),
]


def story_at(time):
    return next(unit for unit in STORY if unit[0] <= time < unit[1])


class _FixtureAuthor:
    name = "synthetic_storyboard_author"
    synthetic = True
    cost_mode = "none"

    def __init__(self):
        self.responses = {}

    def judge(self, request, frames):
        response = self.answer(request, frames)
        self.responses[digest(request)] = response
        return response

    def answer(self, request, frames):
        refs = [f.frame_id for f in frames]
        if request["source_id"] == "synthetic_uncertain":
            return {"decision": "unknown", "reason": "Authored synthetic ambiguity; no independent identity established.",
                    "evidence_frame_ids": refs}
        if request["stage"] == "unit_review":
            start, end = request["focal_interval"]
            inside = [unit for unit in STORY if unit[0] < end and unit[1] > start]
            if len(inside) != 1:
                return {"decision": "unknown", "reason": "Authored interval contains multiple units.",
                        "evidence_frame_ids": refs}
            unit = inside[0]
            return {"decision": "same_unit", "unit": {"unit_id": unit[2], "type": unit[3]},
                    "reason": "Authored synthetic whole-unit review.", "evidence_frame_ids": refs}
        focus = request["focus"]
        before = [f for f in frames if f.time < focus]
        after = [f for f in frames if f.time >= focus]
        if not before or not after:
            return {"decision": "unknown", "reason": "Fixture lacks ordered sides.", "evidence_frame_ids": refs}
        old, new = before[-1], after[0]
        left, right = story_at(old.time), story_at(new.time)
        response = {"decision": "same_unit" if left[2] == right[2] else "new_unit",
                    "left": {"unit_id": left[2], "type": left[3]},
                    "right": {"unit_id": right[2], "type": right[3]},
                    "reason": "Authored synthetic broadcast identity, not an image-change inference.",
                    "evidence_frame_ids": [old.frame_id, new.frame_id]}
        if response["decision"] == "new_unit":
            response["boundary"] = {"last_old_frame_id": old.frame_id, "first_new_frame_id": new.frame_id}
        return response


def generate_fixture(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    frames_dir = output / "frames"
    frames_dir.mkdir()
    origin = 9_000_000
    duration = 12.6
    micros = {-1_000_000, 13_100_000, 0, 12_599_000}
    cursor, step = 0, 0
    while cursor < 12_600_000:
        micros.add(cursor)
        cursor += (71_000, 93_000, 108_000)[step % 3]
        step += 1
    for boundary in [unit[0] for unit in STORY[1:]] + [0.72, 3.2, 4.6, 11.2]:
        value = round(boundary * 1_000_000)
        micros.update([value - 35_000, value])
    frames, outside = [], []
    for index, value in enumerate(sorted(micros)):
        time = value / 1_000_000
        unit = story_at(time)
        color = unit[4]
        # Deliberate within-unit shot/sponsor changes.
        if 0.72 <= time < 2.4 or 3.2 <= time < 4.1 or 4.6 <= time < 5.3 or time >= 11.2:
            color = tuple(255-component for component in color)
        path = frames_dir / ("frame_%04d.ppm" % index)
        path.write_bytes(b"P6\n24 16\n255\n" + bytes(color) * (24*16))
        item = {"frame_id": "f%04d" % index, "pts_us": origin + value,
                "path": path.relative_to(output).as_posix(), "sha256": file_hash(path)}
        (frames if 0 <= time < duration else outside).append(item)
    manifest = {"synthetic": True, "sources": [
        {"source_id": "synthetic_tv", "channel": "SYNTHETIC_CN", "duration": duration,
         "window_start_pts_us": origin, "frames": frames,
         "context": {"frames": outside,
                     "asr": [{"start": -1, "end": -0.2, "text": "节目在窗口之前已开始。"},
                             {"start": 12.7, "end": 13.1, "text": "购物节目继续。"}]},
         "asr": [{"start": 0.72, "end": 1.6, "text": "本节目由云山赞助，扫码了解。主持人继续本期话题。"},
                 {"start": 2.4, "end": 3.2, "text": "云山品牌独立广告第一条。"},
                 {"start": 4.1, "end": 4.6, "text": "云山品牌独立广告第二条。"},
                 {"start": 5.3, "end": 5.64, "text": "频道宣传，精彩继续。"},
                 {"start": 9, "end": 10.4, "text": "公益广告：节约用水。"}],
         "coarse_segments": [{"segment_id": "coarse_all", "start": 0, "end": duration, "type": "program"}]},
        {"source_id": "synthetic_uncertain", "channel": "SYNTHETIC_CN", "duration": 1.3,
         "window_start_pts_us": origin, "frames": frames[:8]},
        {"source_id": "synthetic_missing", "channel": "SYNTHETIC_CN", "duration": 1.1,
         "frames": []}
    ]}
    write_json(output / "manifest.json", manifest)
    write_json(output / "storyboard.json", {"synthetic_only": True,
        "purpose": "Fixture authoring explanation; NEVER loaded by inference.",
        "units": [{"start": a, "end": b, "identity": c, "type": d} for a, b, c, d, _ in STORY]})
    author = _FixtureAuthor()
    with tempfile.TemporaryDirectory(prefix="adseg-fixture-") as temp:
        run(output / "manifest.json", Path(temp) / "author_run", author, Config())
    write_json(output / "replay.json", {"schema": "adseg-replay-v1", "synthetic": True,
        "provenance": "Explicit authored synthetic responses. Contract exercise, NOT accuracy evidence.",
        "responses": author.responses})
    return {"sources": len(manifest["sources"]), "replay_requests": len(author.responses),
            "synthetic": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="new fixture directory")
    args = parser.parse_args(argv)
    try:
        result = generate_fixture(args.output)
    except (ValueError, OSError):
        parser.exit(2, "fixture generation failed; output must be a new directory\n")
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
