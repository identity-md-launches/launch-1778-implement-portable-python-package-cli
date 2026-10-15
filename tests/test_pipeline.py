import copy
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from adseg_auto import Config, ReplayProvider, run
from adseg_auto.candidates import generate
from adseg_auto.fixture import generate_fixture
from adseg_auto.input import read_manifest, ingest
from adseg_auto.pipeline import Engine
from adseg_auto.providers import ProviderError
from adseg_auto.util import digest, file_hash, load, write_json


def rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines()]


class FixedProvider:
    name = "test_injected"
    synthetic = True

    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.requests = answer, error, []

    def judge(self, request, frames):
        self.requests.append(request)
        if self.error:
            raise self.error
        if callable(self.answer):
            return self.answer(request, frames)
        return copy.deepcopy(self.answer)


class FixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="adseg-tests-")
        cls.base = Path(cls.temp.name)
        cls.fixture = cls.base / "fixture"
        generate_fixture(cls.fixture)
        cls.output = cls.base / "replay_run"
        cls.summary = run(cls.fixture / "manifest.json", cls.output,
                          ReplayProvider(cls.fixture / "replay.json"))
        cls.raw = rows(cls.output / "source_0000/raw.jsonl")
        cls.ledger = rows(cls.output / "decisions.jsonl")
        cls.report = load(cls.output / "failure_report.json")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_within_ad_shot_change_is_merged(self):
        self.assertTrue(any(r["type"] == "ad" and r["start"] < 3.2 < r["end"] for r in self.raw))
        shot = [e for e in self.ledger if e["event"] == "candidate_decision" and e["proposed_time"] == 3.2]
        self.assertEqual(shot[0]["decision"], "same_unit")

    def test_distinct_consecutive_same_brand_ads(self):
        ads = [r for r in self.raw if r["type"] == "ad"]
        self.assertEqual([r["start"] for r in ads], [2.4, 4.1, 9.0])
        identities = [e["unit_id"] for e in self.ledger if e["event"] == "reconciled_unit" and e["type"] == "ad"]
        self.assertIn("yunshan_ad_broadcast_1", identities)
        self.assertIn("yunshan_ad_broadcast_2", identities)

    def test_short_promotion_is_preserved_in_raw_and_floor(self):
        promo = next(r for r in self.raw if r["start"] == 5.3)
        self.assertEqual(promo["type"], "program")
        self.assertLess(promo["end"] - promo["start"], 0.4)
        floor = rows(self.output / "source_0000/floor.jsonl")
        quantized = next(r for r in floor if r["segment_id"] == promo["segment_id"])
        self.assertEqual(quantized["start"], quantized["end"])
        self.assertTrue(any(w.get("segment_id") == promo["segment_id"] for w in self.report["quantization_warnings"]))

    def test_program_trailers_and_promotions_remain_distinct(self):
        starts = [r["start"] for r in self.raw if r["type"] == "program"]
        self.assertEqual(starts, [0, 5.3, 5.64, 7.3, 10.4])

    def test_integrated_sponsorship_is_program(self):
        first = self.raw[0]
        self.assertEqual(first["type"], "program")
        self.assertTrue(first["start"] < 0.72 < first["end"])
        decision = next(e for e in self.ledger if e["event"] == "candidate_decision" and e["proposed_time"] == 0.72)
        self.assertEqual(decision["decision"], "same_unit")

    def test_outside_context_informs_without_extending_output(self):
        refs = [f for e in self.ledger if e["event"] == "semantic_attempt"
                for f in e["request"]["evidence"]["ordered_frames"]]
        self.assertTrue(any(f["time"] == -1 and f["pts_us"] == 8_000_000 for f in refs))
        self.assertTrue(any(f["time"] == 13.1 for f in refs))
        self.assertEqual(self.raw[0]["start"], 0)
        self.assertEqual(self.raw[-1]["end"], 12.6)

    def test_variable_pts_and_hash_binding_without_false_native_claim(self):
        manifest = load(self.fixture / "manifest.json")
        frames = {f["frame_id"]: f for f in manifest["sources"][0]["frames"]}
        pts = sorted(f["pts_us"] for f in frames.values())
        self.assertGreater(len({b-a for a,b in zip(pts,pts[1:])}), 3)
        boundaries = [e["boundary"] for e in self.ledger if e["event"] == "candidate_decision" and "boundary" in e]
        for boundary in boundaries:
            self.assertFalse(boundary["native_adjacent_verified"])
            for side in ("last_old", "first_new"):
                ref = boundary[side]
                original = frames[ref["frame_id"]]
                self.assertEqual(ref["sha256"], file_hash(self.fixture / original["path"]))
                self.assertEqual(ref["time"], (original["pts_us"] - 9_000_000) / 1_000_000)
            self.assertEqual(boundary["cut_seconds"], boundary["first_new"]["time"])

    def test_sparse_brackets_are_unknown(self):
        for event in self.ledger:
            if event["event"] == "candidate_decision" and "boundary" in event:
                start, end = event["boundary"]["uncertainty_interval"]
                covering = [r for r in self.raw if r["start"] < end and r["end"] > start]
                self.assertTrue(covering)
                self.assertTrue(all(r["type"] == "unknown" for r in covering))

    def test_missing_frames_and_semantic_unknown_are_distinguished(self):
        candidate = load(self.output / "candidate_manifest.json")
        cases = candidate["cases"]
        self.assertEqual(set(cases["synthetic_missing"]["unknown_causes"].values()), {"technical"})
        self.assertEqual(set(cases["synthetic_uncertain"]["unknown_causes"].values()), {"semantic"})
        unknown = rows(self.output / "source_0001/raw.jsonl")
        self.assertEqual([(r["start"], r["end"], r["type"]) for r in unknown], [(0,1.3,"unknown")])
        self.assertTrue(any(e.get("stopping_reason") == "evidence_exhausted" for e in self.ledger))

    def test_full_coverage_raw_floor_and_actual_hashes(self):
        candidate = load(self.output / "candidate_manifest.json")
        manifest = load(self.fixture / "manifest.json")
        self.assertEqual(candidate["origin"], "synthetic_contract_fixture")
        self.assertEqual(set(candidate["cases"]), {s["source_id"] for s in manifest["sources"]})
        for source in manifest["sources"]:
            case = candidate["cases"][source["source_id"]]
            raw, floor = [rows(self.output / case[k]["path"]) for k in ("raw", "floor")]
            self.assertEqual(len(raw), len(floor))
            previous = 0
            for a,b in zip(raw,floor):
                self.assertEqual(set(a), {"source_id", "segment_id", "start", "end", "type"})
                self.assertEqual(a["start"], previous)
                self.assertGreater(a["end"], a["start"])
                self.assertTrue(math.isfinite(a["end"]))
                self.assertEqual([b[k] for k in ("start","end")], [math.floor(a[k]) for k in ("start","end")])
                for key in ("source_id","segment_id","type"):
                    self.assertEqual(a[key], b[key])
                previous = a["end"]
            self.assertEqual(previous, source["duration"])
            self.assertEqual(set(case["unknown_causes"]), {r["segment_id"] for r in raw if r["type"] == "unknown"})
            for kind in ("raw", "floor"):
                self.assertEqual(file_hash(self.output / case[kind]["path"]), case[kind]["sha256"])

    def test_every_candidate_gets_a_semantic_judgment(self):
        sources, _ = read_manifest(self.fixture / "manifest.json")
        ingest(sources[0])
        candidates = generate(sources[0], Config())
        decisions = [e for e in self.ledger if e["source_id"] == "synthetic_tv" and e["event"] == "candidate_decision"]
        self.assertEqual([c.candidate_id for c in candidates], [e["request_id"] for e in decisions])
        self.assertTrue(any("regular_probe" in e["cues"] for e in decisions))

    def test_stable_ids_and_replay_are_path_independent(self):
        other = self.base / "replay_again"
        run(self.fixture / "manifest.json", other, ReplayProvider(self.fixture / "replay.json"))
        self.assertEqual((self.output / "source_0000/raw.jsonl").read_bytes(), (other / "source_0000/raw.jsonl").read_bytes())
        self.assertEqual(self.summary["private_metrics"], "NOT EVALUATED")
        self.assertEqual(self.summary["held_out_metrics"], "NOT EVALUATED")
        self.assertEqual(self.summary["http_attempts"], 0)


class FailureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="adseg-failure-tests-")
        self.base = Path(self.temp.name)
        frame = self.base / "one.ppm"
        frame.write_bytes(b"P6\n2 2\n255\n" + bytes((20,30,40))*4)
        self.data = {"sources": [{"source_id": "single", "channel": "synthetic", "duration": 1.5,
                                 "frames": [{"frame_id": "one", "pts_us": 0, "path": "one.ppm", "sha256": file_hash(frame)},
                                            {"frame_id": "two", "pts_us": 1_400_000, "path": "one.ppm", "sha256": file_hash(frame)}]}]}
        self.path = self.base / "manifest.json"
        write_json(self.path, self.data)
        self.counter = 0

    def tearDown(self):
        self.temp.cleanup()

    def execute(self, provider=None, config=None):
        self.counter += 1
        out = self.base / ("run%d" % self.counter)
        result = run(self.path, out, provider, config)
        return out, result

    def test_provider_failure_and_malformed_answers_stay_technical_unknown(self):
        providers = [FixedProvider(error=RuntimeError("never-log-this-private-marker")),
                     FixedProvider(error=ProviderError({"bad":"never-log-this-private-marker"})),
                     FixedProvider(None), FixedProvider({"decision":"probably_ad"}),
                     FixedProvider({"decision":"same_unit", "unit":{"unit_id":"x","type":"ad"},
                                    "reason":"unsupported", "evidence_frame_ids":["invented"]})]
        for provider in providers:
            out, summary = self.execute(provider)
            row = rows(out / "source_0000/raw.jsonl")[0]
            self.assertEqual(row["type"], "unknown")
            causes = load(out / "candidate_manifest.json")["cases"]["single"]["unknown_causes"]
            self.assertEqual(causes[row["segment_id"]], "technical")
            self.assertGreater(summary["technical_failure_count"], 0)
            for p in out.rglob("*.json*"):
                self.assertNotIn("never-log-this-private-marker", p.read_text())

    def test_no_paid_calls_by_default(self):
        out, summary = self.execute()
        self.assertEqual(summary["http_attempts"], 0)
        self.assertEqual(rows(out / "source_0000/raw.jsonl")[0]["type"], "unknown")

    def test_injected_provider_unreported_cost_is_not_assumed_free(self):
        provider = FixedProvider({"decision":"unknown","reason":"insufficient","evidence_frame_ids":[]})
        # Custom provider internals are not the HTTP adapter's sanitized schema.
        provider.config = {"private":"never-copy-injected-provider-internals"}
        provider.receipts = [{"Authorization":"never-copy-injected-provider-internals"}]
        out, summary = self.execute(provider)
        self.assertIsNone(summary["known_cost"])
        self.assertIsNone(summary["cost_charged_or_reserved"])
        self.assertEqual(summary["unknown_cost_invocations"],summary["semantic_invocations"])
        self.assertEqual(rows(out / "response_receipts.jsonl")[0]["cost_status"],"unknown_injected_provider_cost")
        self.assertEqual((out / "http_attempt_receipts.jsonl").read_text(),"")
        for path in out.rglob("*.json*"):
            self.assertNotIn("never-copy-injected-provider-internals",path.read_text())

    def test_no_semantic_inference_from_coarse_labels(self):
        self.data["sources"][0]["coarse_segments"] = [{"segment_id":"all","start":0,"end":1.5,"type":"ad"}]
        write_json(self.path, self.data)
        out, _ = self.execute()
        self.assertEqual(rows(out / "source_0000/raw.jsonl")[0]["type"], "unknown")

    def test_corrupt_and_missing_frame_retains_source(self):
        for kind in ("sha256", "path"):
            altered = copy.deepcopy(self.data)
            altered["sources"][0]["frames"][0][kind] = "incorrect"
            write_json(self.path, altered)
            out, summary = self.execute()
            self.assertEqual(summary["sources"][0]["technical_source_failure"], True)
            self.assertEqual(rows(out / "source_0000/raw.jsonl")[0]["end"], 1.5)

    def test_refuse_overwriting_even_empty_directory(self):
        out = self.base / "exists"
        out.mkdir()
        sentinel = out / "sentinel"
        sentinel.write_text("keep")
        with self.assertRaises(FileExistsError):
            run(self.path, out)
        self.assertEqual(sentinel.read_text(), "keep")

    def test_replay_does_not_guess_for_unrecorded_requests(self):
        replay = self.base / "replay.json"
        write_json(replay, {"schema":"adseg-replay-v1", "responses":{}})
        out, _ = self.execute(ReplayProvider(replay))
        report = load(out / "failure_report.json")
        self.assertEqual(report["technical_failures"][0]["code"], "REPLAY_REQUEST_NOT_FOUND")

    def test_invalid_manifest_or_config_is_rejected_before_output(self):
        for duration in (True, 0, -1, float("inf")):
            altered = copy.deepcopy(self.data)
            altered["sources"][0]["duration"] = duration
            self.path.write_text(json.dumps(altered))
            out = self.base / "invalid-output"
            with self.assertRaises(ValueError):
                run(self.path, out)
            self.assertFalse(out.exists())
        for config in (Config(probe_interval=0), Config(max_context=1), Config(max_evidence_frames=2)):
            with self.assertRaises(ValueError):
                config.validate()

    def test_gt_scores_and_answers_are_never_forwarded(self):
        self.data["answers"] = "forbidden-top-level-marker"
        self.data["sources"][0].update(gt="forbidden-gt-marker", scores="forbidden-score-marker", answers="forbidden-answer-marker")
        write_json(self.path, self.data)
        provider = FixedProvider({"decision":"unknown","reason":"insufficient","evidence_frame_ids":[]})
        out, _ = self.execute(provider)
        combined = json.dumps(provider.requests) + (out / "decisions.jsonl").read_text()
        for marker in ("forbidden-top-level-marker", "forbidden-gt-marker", "forbidden-score-marker", "forbidden-answer-marker"):
            self.assertNotIn(marker, combined)

    def test_missing_media_tools_are_clear_and_local(self):
        self.data["sources"][0]["frames"] = []
        self.data["sources"][0]["video_path"] = "operator-video.mp4"
        write_json(self.path, self.data)
        with patch("adseg_auto.media.shutil.which", return_value=None):
            out, _ = self.execute()
        codes = {e["code"] for e in load(out / "failure_report.json")["technical_failures"]}
        self.assertIn("MEDIA_TOOLS_MISSING", codes)

    def test_candidate_resource_limit_is_explicit_failure(self):
        out, _ = self.execute(config=Config(probe_interval=0.01,max_candidates=10))
        self.assertEqual(rows(out / "source_0000/raw.jsonl")[0]["type"], "unknown")
        self.assertEqual(load(out / "failure_report.json")["technical_failures"][0]["code"], "CANDIDATE_OR_EVIDENCE_PROCESSING_FAILED")

    def test_context_expands_with_bounds(self):
        source = self.data["sources"][0]
        source["context"] = {"frames":[{**source["frames"][0],"frame_id":"far","pts_us":5_000_000}]}
        write_json(self.path, self.data)
        provider = FixedProvider({"decision":"unknown", "reason":"insufficient", "evidence_frame_ids":[]})
        out, _ = self.execute(provider, Config(initial_context=0.1,max_context=6,max_expansions=6))
        # Once no additional evidence is found, expansion stops explicitly.
        attempts = [e for e in rows(out / "decisions.jsonl") if e["event"] == "semantic_attempt"]
        self.assertGreaterEqual(len(attempts), 1)
        self.assertLessEqual(len(attempts), 7)
        self.assertTrue(any(f["frame_id"] == "far" for r in provider.requests for f in r["evidence"]["ordered_frames"]))


if __name__ == "__main__":
    unittest.main()
