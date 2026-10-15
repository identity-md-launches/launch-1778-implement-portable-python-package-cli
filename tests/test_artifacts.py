import copy
from pathlib import Path
import tempfile
import unittest
import zipfile
import subprocess
import os

import adseg_build
from adseg_auto.fixture import generate_fixture
from adseg_auto.pipeline import run
from adseg_auto.providers import ReplayProvider
from adseg_auto.util import load, write_json, file_hash
from adseg_auto.verify import verify


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="adseg-artifact-tests-")
        self.base = Path(self.temp.name)
        self.fixture = self.base / "fixture"
        generate_fixture(self.fixture)
        self.out = self.base / "run"
        run(self.fixture / "manifest.json", self.out, ReplayProvider(self.fixture / "replay.json"))
        self.candidate = self.out / "candidate_manifest.json"

    def tearDown(self):
        self.temp.cleanup()

    def test_local_verifier_accepts_contract_and_keeps_quantization_warnings(self):
        report = verify(self.fixture / "manifest.json", self.candidate)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(len(report["sources"]), 3)
        self.assertEqual(len(report["warnings"]), 10)

    def test_tampering_source_omission_and_bad_unknown_causes_are_rejected(self):
        original = load(self.candidate)
        for change in ("missing_source", "hash", "causes", "traversal"):
            altered = copy.deepcopy(original)
            case = altered["cases"]["synthetic_tv"]
            if change == "missing_source":
                del altered["cases"]["synthetic_missing"]
            elif change == "hash":
                case["raw"]["sha256"] = "incorrect"
            elif change == "causes":
                case["unknown_causes"] = {}
            else:
                case["raw"]["path"] = "../run/source_0000/raw.jsonl"
            write_json(self.candidate, altered)
            self.assertEqual(verify(self.fixture / "manifest.json", self.candidate)["status"],"failed")

    def test_output_hash_index_binds_all_outputs_but_itself(self):
        hashes = load(self.out / "hashes.json")
        self.assertEqual(hashes["input_manifest_sha256"], file_hash(self.fixture / "manifest.json"))
        for relative, expected in hashes["outputs"].items():
            self.assertEqual(file_hash(self.out / relative),expected)
        self.assertNotIn("hashes.json", hashes["outputs"])

    def test_dependency_free_wheel_runs_outside_repository(self):
        wheel = self.base / adseg_build.build_wheel(self.base)
        with zipfile.ZipFile(wheel) as archive:
            self.assertIn("adseg_auto/__main__.py",archive.namelist())
            self.assertIn("adseg_auto-1.0.0.dist-info/RECORD",archive.namelist())
            archive.extractall(self.base / "installed")
        # Only point this child at the just-built ordinary files, not the source tree.
        child = subprocess.run([os.sys.executable,"-B","-m","adseg_auto","--help"],
                               cwd=self.base / "installed", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=10, check=False)
        self.assertEqual(child.returncode,0,child.stderr)
        self.assertIn(b"--provider",child.stdout)


if __name__ == "__main__":
    unittest.main()
