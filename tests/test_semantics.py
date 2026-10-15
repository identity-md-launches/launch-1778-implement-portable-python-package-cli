import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from adseg_auto.input import Source, Frame
from adseg_auto.media import decode_video
from adseg_auto.pipeline import Config, Engine
from adseg_auto.util import file_hash


class SemanticAndNativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="adseg-semantic-tests-")
        self.base = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def source(self, times, cut=None, native=True):
        source = Source("synthetic", "synthetic", times[-1] + 0.1, 20_000_000, {}, self.base)
        for index, time in enumerate(times):
            path = self.base / ("f%d.ppm" % index)
            color = (20,30,40) if cut is None or time < cut else (220,200,180)
            path.write_bytes(b"P6\n2 2\n255\n" + bytes(color)*4)
            source.frames.append(Frame("f%d" % index, 20_000_000+round(time*1e6), time, path,
                                       file_hash(path), index if native else None,
                                       "synthetic_decoded_sequence" if native else None))
        return source

    def test_refine_to_actual_adjacent_variable_pts_frames(self):
        source = self.source([0, 0.1, 0.235, 0.271, 0.4, 0.5], cut=0.271)
        calls = []
        class Provider:
            name, synthetic = "synthetic_native_test", True
            def judge(self, request, frames):
                calls.append(request)
                refs = [f.frame_id for f in frames]
                left, right = {"unit_id":"same_brand_ad_1","type":"ad"}, {"unit_id":"same_brand_ad_2","type":"ad"}
                if request["stage"] == "unit_review":
                    return {"decision":"same_unit", "unit": left if request["focus"] < 0.271 else right,
                            "evidence_frame_ids":refs,"reason":"synthetic whole unit"}
                old, new = ("f2","f3") if request["stage"] == "boundary_refinement" else ("f1","f4")
                return {"decision":"new_unit", "left":left,"right":right,"evidence_frame_ids":refs,
                        "boundary":{"last_old_frame_id":old,"first_new_frame_id":new},"reason":"synthetic separate broadcasts"}
        engine = Engine(Provider(), Config(probe_interval=10))
        pieces = engine.segment(source)
        self.assertEqual([(p["start"],p["end"],p["type"]) for p in pieces], [(0,0.271,"ad"),(0.271,0.6,"ad")])
        self.assertTrue(any(c["stage"] == "boundary_refinement" for c in calls))
        cut = next(e["boundary"] for e in engine.ledger if e["event"] == "candidate_decision")
        self.assertTrue(cut["native_adjacent_verified"])
        self.assertEqual(cut["last_old"]["pts_us"], 20_235_000)
        self.assertEqual(cut["first_new"]["pts_us"], 20_271_000)
        self.assertIsNone(cut["uncertainty_interval"])

    def test_conflicting_neighbor_class_claims_become_semantic_unknown(self):
        source = self.source([0,0.5,1,1.5,2,2.5,3])
        class Provider:
            name, synthetic = "synthetic_conflict_test", True
            def judge(self, request, frames):
                unit = {"unit_id":"one", "type":"program" if request["focus"] == 2 else "ad"}
                response = {"decision":"same_unit","evidence_frame_ids":[f.frame_id for f in frames],"reason":"synthetic conflict"}
                response.update({"unit":unit} if request["stage"] == "unit_review" else {"left":unit,"right":unit})
                return response
        engine = Engine(Provider(), Config(probe_interval=1))
        pieces = engine.segment(source)
        self.assertTrue(all(p["type"] == "unknown" and p["cause"] == "semantic" for p in pieces))
        self.assertIn("NEIGHBOR_IDENTITY_OR_CLASS_CONFLICT", {u["code"] for u in engine.uncertainties})

    def test_same_unit_cannot_claim_two_identities(self):
        source = self.source([0,0.5,1,1.5])
        class Provider:
            name, synthetic = "synthetic_bad_identity", True
            def judge(self, request, frames):
                if request["stage"] == "unit_review":
                    return {"decision":"same_unit","unit":{"unit_id":"x","type":"program"},
                            "evidence_frame_ids":[f.frame_id for f in frames],"reason":"synthetic"}
                return {"decision":"same_unit","left":{"unit_id":"x","type":"program"},
                        "right":{"unit_id":"y","type":"program"},
                        "evidence_frame_ids":[f.frame_id for f in frames],"reason":"contradiction"}
        engine = Engine(Provider(), Config(probe_interval=1))
        pieces = engine.segment(source)
        self.assertTrue(all(p["type"] == "unknown" and p["cause"] == "technical" for p in pieces))

    def test_media_uses_arrays_and_probe_pts_not_fps_or_global_offset(self):
        video = self.base / "video with spaces; literal.mp4"
        video.write_bytes(b"synthetic-media-adapter-test")
        source = Source("synthetic", "synthetic", 0.5, 20_000_000,
                        {"video_path":video.name}, self.base)
        commands = []
        def fake_run(args, **kwargs):
            self.assertIsInstance(args, list)
            self.assertNotIn("shell", kwargs)
            self.assertIn(str(video), args)
            commands.append(args)
            if "-show_frames" in args:
                data = {"streams":[{"time_base":"1/1000000"}],
                        "frames":[{"pts":20_000_000},{"pts":20_037_000},{"pts":20_081_000}]}
                return subprocess.CompletedProcess(args, 0, json.dumps(data).encode(), b"")
            pattern = args[-1]
            for index in range(3):
                Path(pattern.replace("%08d", "%08d" % index)).write_bytes(b"P6\n2 2\n255\n" + bytes((index,20,30))*4)
            return subprocess.CompletedProcess(args, 0, b"", b"")
        with patch("adseg_auto.media.shutil.which", side_effect=lambda name:name), patch("adseg_auto.media.subprocess.run", side_effect=fake_run):
            decode_video(source, self.base / "decoded", 1, 100, 2)
        self.assertEqual([f.time for f in source.frames], [0,0.037,0.081])
        self.assertEqual([f.native_index for f in source.frames], [0,1,2])
        self.assertEqual(source.frames[1].ref()["native_pts_ticks"],20_037_000)
        self.assertEqual(source.frames[1].ref()["native_time_base"],"1/1000000")
        self.assertTrue((self.base / "decoded/pts_index.json").is_file())
        self.assertEqual(len(commands), 2)


if __name__ == "__main__":
    unittest.main()
