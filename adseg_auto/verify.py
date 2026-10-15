"""Read-only local artifact contract check. Does not score content or accuracy."""

import argparse
import math
from pathlib import Path, PureWindowsPath

from .input import read_manifest
from .util import encoded, file_hash, load, loads, number, text


def verify(manifest, candidate_manifest):
    result = {"status": "failed", "scope": "artifact_structure_only", "errors": [],
              "warnings": [], "sources": [], "private_metrics": "NOT EVALUATED",
              "held_out_metrics": "NOT EVALUATED"}
    try:
        sources, _ = read_manifest(manifest)
        base = Path(candidate_manifest).resolve().parent
        candidate = load(candidate_manifest)
        if candidate.get("schema") != "local-candidate-evaluation-v1":
            raise ValueError("wrong candidate schema")
        if candidate.get("candidate_id") != "portable_auto_v1":
            raise ValueError("wrong candidate_id")
        if candidate.get("origin") not in ("synthetic_contract_fixture", "unverified_model_output"):
            raise ValueError("wrong origin")
        text(candidate.get("provenance_note"), "provenance_note")
        cases = candidate.get("cases")
        if not isinstance(cases, dict) or set(cases) != {s.source_id for s in sources}:
            raise ValueError("candidate source set differs from supplied sources")
        for source in sources:
            try:
                case = cases[source.source_id]
                outputs = {}
                for kind in ("raw", "floor"):
                    descriptor = case[kind]
                    relative = text(descriptor["path"], "artifact path")
                    path = Path(relative)
                    if (path.is_absolute() or PureWindowsPath(relative).drive or "\\" in relative
                            or ":" in relative or ".." in path.parts):
                        raise ValueError("artifact path must be portable and relative")
                    resolved = (base / path).resolve(strict=True)
                    resolved.relative_to(base)
                    if file_hash(resolved) != descriptor["sha256"]:
                        raise ValueError("artifact hash mismatch")
                    lines = resolved.read_text(encoding="utf-8").splitlines()
                    if not lines or any(not line.strip() for line in lines):
                        raise ValueError("empty artifact or blank row")
                    rows = [loads(line) for line in lines]
                    previous, ids = 0, set()
                    duration = source.duration if kind == "raw" else math.floor(source.duration)
                    for row in rows:
                        if not isinstance(row, dict) or set(row) != {"source_id","segment_id","start","end","type"}:
                            raise ValueError("row fields differ from contract")
                        if row["source_id"] != source.source_id or row["type"] not in ("ad","program","unknown"):
                            raise ValueError("invalid row source/class")
                        sid = text(row["segment_id"], "segment_id")
                        if sid in ids:
                            raise ValueError("duplicate segment_id")
                        ids.add(sid)
                        start, end = number(row["start"],"start"), number(row["end"],"end")
                        if start != previous or start < 0 or end > duration or end < start or kind == "raw" and end == start:
                            raise ValueError("intervals must provide ordered, exact full coverage")
                        previous = end
                    if previous != duration:
                        raise ValueError("duration is not fully covered")
                    outputs[kind] = rows
                if len(outputs["raw"]) != len(outputs["floor"]):
                    raise ValueError("raw/floor row count differs")
                for raw, floor in zip(outputs["raw"], outputs["floor"]):
                    if floor != {**raw,"start":math.floor(raw["start"]),"end":math.floor(raw["end"])}:
                        raise ValueError("floor must preserve row order, IDs, types and floor every endpoint")
                    if floor["start"] == floor["end"]:
                        result["warnings"].append({"source_id":source.source_id,"segment_id":raw["segment_id"],
                                                   "code":"QUANTIZATION_ZERO_LENGTH"})
                if math.floor(source.duration) != source.duration:
                    result["warnings"].append({"source_id":source.source_id,"code":"QUANTIZATION_DURATION"})
                unknown = {r["segment_id"] for r in outputs["raw"] if r["type"] == "unknown"}
                causes = case["unknown_causes"]
                if not isinstance(causes, dict) or set(causes) != unknown or any(c not in ("technical","semantic") for c in causes.values()):
                    raise ValueError("unknown_causes must exactly cover unknown IDs")
                result["sources"].append({"source_id":source.source_id,"rows":len(outputs["raw"]),"unknown_rows":len(unknown)})
            except (ValueError, OSError, TypeError, KeyError, AttributeError):
                result["errors"].append({"source_id":source.source_id,"code":"ARTIFACT_CONTRACT_VIOLATION"})
    except (ValueError, OSError, TypeError, KeyError, AttributeError):
        result["errors"].append({"code":"INVALID_MANIFEST_OR_ARTIFACT_INDEX"})
    if not result["errors"]:
        result["status"] = "passed"
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-manifest", required=True)
    parser.add_argument("--candidate-manifest", required=True)
    args = parser.parse_args(argv)
    report = verify(args.input_manifest, args.candidate_manifest)
    print(encoded(report).decode("utf-8"))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
