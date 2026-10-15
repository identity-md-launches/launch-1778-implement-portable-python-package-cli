"""Proposal -> semantic adjudication -> reconciliation -> evidence-bound outputs."""

from dataclasses import asdict, dataclass
import math
from pathlib import Path
import time

from .candidates import evidence, generate
from .input import read_manifest, ingest
from .media import decode_video
from .providers import HTTPProvider, OfflineProvider, ReplayProvider, ProviderError, sanitized_response, POLICY
from .util import digest, encoded, file_hash, number, write_json, write_jsonl


@dataclass
class Config:
    probe_interval: float = 2.0
    image_change_threshold: float = 0.12
    initial_context: float = 2.0
    max_context: float = 16.0
    max_expansions: int = 3
    max_evidence_frames: int = 48
    max_candidates: int = 10000
    max_native_frames: int = 120000
    media_timeout: float = 180.0
    max_refinement_steps: int = 4

    def validate(self):
        for name in ("probe_interval", "initial_context", "max_context", "media_timeout"):
            number(getattr(self, name), name, positive=True)
        number(self.image_change_threshold, "image_change_threshold")
        if not 0 <= self.image_change_threshold <= 1:
            raise ValueError("image_change_threshold must be 0..1")
        if self.initial_context > self.max_context:
            raise ValueError("initial_context exceeds max_context")
        for name in ("max_expansions", "max_evidence_frames", "max_candidates", "max_native_frames", "max_refinement_steps"):
            value = getattr(self, name)
            if type(value) is not int or value < (0 if name == "max_expansions" else 1):
                raise ValueError(name + " must be a bounded integer")
        if self.max_evidence_frames < 12 or self.max_expansions > 10:
            raise ValueError("at least 12 evidence frames; at most 10 expansions")
        if self.max_refinement_steps > 16:
            raise ValueError("at most 16 native refinement steps")


def _unit(value):
    if (not isinstance(value, dict) or not isinstance(value.get("unit_id"), str)
            or not value["unit_id"].strip() or value.get("type") not in ("ad", "program", "unknown")):
        raise ValueError("invalid unit identity/class")
    return {"unit_id": value["unit_id"], "type": value["type"]}


def validate_response(response, request):
    if not isinstance(response, dict) or response.get("decision") not in ("same_unit", "new_unit", "unknown"):
        raise ValueError("invalid decision")
    if not isinstance(response.get("reason"), str) or not response["reason"].strip():
        raise ValueError("missing reason")
    refs = response.get("evidence_frame_ids")
    actual = {r["frame_id"]: r for r in request["evidence"]["ordered_frames"]}
    if not isinstance(refs, list) or any(not isinstance(r, str) or r not in actual for r in refs):
        raise ValueError("unbound evidence references")
    decision = response["decision"]
    if decision == "unknown":
        return
    if not refs:
        raise ValueError("supported decisions require actual frame evidence")
    if request["stage"] == "unit_review":
        if decision != "same_unit":
            raise ValueError("unit review must establish one unit or unknown")
        _unit(response.get("unit"))
        return
    if request["stage"] == "boundary_refinement" and decision != "new_unit":
        raise ValueError("refinement must retain the supported independent boundary or abstain")
    left, right = _unit(response.get("left")), _unit(response.get("right"))
    if decision == "same_unit" and left != right:
        raise ValueError("same_unit contradicts identity or class")
    if decision == "new_unit":
        if left["unit_id"] == right["unit_id"]:
            raise ValueError("new_unit needs distinct content-instance identities")
        boundary = response.get("boundary")
        if not isinstance(boundary, dict):
            raise ValueError("missing boundary evidence")
        old, new = boundary.get("last_old_frame_id"), boundary.get("first_new_frame_id")
        if old not in actual or new not in actual or old not in refs or new not in refs:
            raise ValueError("boundary frames must be cited evidence")
        if not actual[old]["pts_us"] < actual[new]["pts_us"]:
            raise ValueError("boundary pair must be ordered")
        if not 0 < actual[new]["time"] < request["duration"]:
            raise ValueError("cut is outside scored window")
        # A candidate may refine within its neighboring proposal interval only.
        lo, hi = request["focal_interval"]
        if not lo <= actual[new]["time"] <= hi:
            raise ValueError("boundary moved beyond neighboring candidates")
        if request["stage"] == "boundary_refinement":
            bracket = request["refinement_bracket"]
            if not bracket[0] <= actual[old]["time"] < actual[new]["time"] <= bracket[1]:
                raise ValueError("refinement escaped the supported bracket")


class Engine:
    def __init__(self, provider, config):
        self.provider, self.config = provider, config
        self.ledger, self.receipts, self.failures, self.uncertainties = [], [], [], []
        self.semantic_calls = 0

    def ask(self, source, key, stage, start, end, focus, extra=None):
        cfg = self.config
        radius = cfg.initial_context
        widest, _ = evidence(source, start, end, focus, cfg.max_context, cfg.max_evidence_frames)
        for attempt in range(cfg.max_expansions + 1):
            gathered, frames = evidence(source, start, end, focus, radius, cfg.max_evidence_frames)
            # Source interval and context limits are explicit even outside window.
            request = {"schema": "adseg-semantic-request-v1", "policy_sha256": digest(POLICY),
                       "source_id": source.source_id, "channel": source.channel,
                       "duration": source.duration, "request_id": key,
                       "stage": stage, "expansion": attempt, "focus": focus,
                       "focal_interval": [start, end], "evidence": gathered,
                       **(extra or {})}
            exhausted = (gathered["available_frame_count"] == widest["available_frame_count"]
                         and gathered["asr"] == widest["asr"]
                         and gathered["coarse_hints"] == widest["coarse_hints"])
            self.semantic_calls += 1
            started = time.monotonic()
            technical, response = None, None
            try:
                response = self.provider.judge(request, frames)
                validate_response(response, request)
            except ProviderError as exc:
                # Error messages are deliberately not copied; providers can echo secrets.
                code = exc.code if isinstance(exc.code, str) else "PROVIDER_FAILURE"
                technical = (code if code in _SAFE_ERRORS or len(code) == 15
                             and code.startswith("HTTP_STATUS_") and code[12:].isdigit()
                             and 100 <= int(code[12:]) <= 599 else "PROVIDER_FAILURE")
            except (ValueError, TypeError, KeyError, IndexError, AttributeError):
                technical = "PROVIDER_MALFORMED_ANSWER"
            except Exception:
                technical = "PROVIDER_EXCEPTION"
            clean = sanitized_response(response)
            try:
                response_hash = digest(clean)
                original_response_hash = digest(response)
            except (ValueError, TypeError, RecursionError):
                clean, response_hash = None, digest(None)
                original_response_hash = None
                technical = "PROVIDER_MALFORMED_ANSWER"
            receipt = {"source_id": source.source_id, "request_id": key,
                       "expansion": attempt, "provider": self.provider.name,
                       "request_sha256": digest(request), "response_sha256": original_response_hash,
                       "sanitized_response_sha256": response_hash,
                       "sanitized_response": clean, "technical_error": technical,
                       "runtime_seconds": time.monotonic() - started,
                       "cost_status": ("see_http_attempt_receipts" if isinstance(self.provider, HTTPProvider)
                                       else "declared_no_live_charge" if getattr(self.provider, "cost_mode", None) == "none"
                                       else "unknown_injected_provider_cost")}
            self.receipts.append(receipt)
            if technical:
                stop = "technical_failure"
            elif response["decision"] != "unknown":
                stop = "semantic_decision_supported"
            elif radius >= cfg.max_context:
                stop = "max_context"
            elif exhausted:
                stop = "evidence_exhausted"
            elif attempt == cfg.max_expansions:
                stop = "max_expansions"
            else:
                stop = "expand_context"
            self.ledger.append({"source_id": source.source_id, "request_id": key,
                                "event": "semantic_attempt", "request": request,
                                "response": clean, "stopping_reason": stop,
                                "technical_error": technical})
            if technical:
                self.failures.append({"source_id": source.source_id, "request_id": key, "code": technical})
                return {"decision": "unknown", "cause": "technical"}
            if response["decision"] != "unknown":
                return response
            if stop != "expand_context":
                self.ledger.append({"source_id": source.source_id, "request_id": key,
                                    "event": "context_stop", "stopping_reason": stop})
                break
            radius = min(cfg.max_context, radius * 2)
        self.uncertainties.append({"source_id": source.source_id, "request_id": key, "code": "SEMANTIC_UNKNOWN"})
        return {"decision": "unknown", "cause": "semantic"}

    def segment(self, source):
        candidates = generate(source, self.config)
        times = [0] + [c.time for c in candidates] + [source.duration]
        judged, boundaries, ambiguity = [], [], []
        by_id = {f.frame_id: f for f in source.frames}
        for index, candidate in enumerate(candidates):
            response = self.ask(source, candidate.candidate_id, "candidate", times[index], times[index+2],
                                candidate.time, {"candidate": {"time": candidate.time, "cues": candidate.cues},
                                "left_interval": [times[index], candidate.time],
                                "right_interval": [candidate.time, times[index+2]]})
            judged.append((candidate, response))
            decision = response["decision"]
            record = {"source_id": source.source_id, "request_id": candidate.candidate_id,
                      "event": "candidate_decision", "proposed_time": candidate.time,
                      "cues": candidate.cues, "decision": decision}
            if decision == "new_unit":
                response, refinement_cause = self.refine(source, candidate.candidate_id, response)
                judged[-1] = (candidate, response)
                pair = response["boundary"]
                old, new = by_id[pair["last_old_frame_id"]], by_id[pair["first_new_frame_id"]]
                native = (old.native_sequence is not None and old.native_sequence == new.native_sequence
                          and new.native_index == old.native_index + 1)
                cut = new.time
                record.update(boundary={"last_old": old.ref(), "first_new": new.ref(),
                              "cut_seconds": cut, "native_adjacent_verified": native,
                              "precision": "native_frame" if native else "sparse_bracket_only",
                              "uncertainty_interval": None if native else [max(0, old.time), cut],
                              "limitation": None if native else "Sparse frames cannot prove the native cut; bracket is unknown."})
                boundaries.append({"time": cut, "response": response, "candidate_id": candidate.candidate_id})
                if not native and max(0, old.time) < cut:
                    ambiguity.append((max(0, old.time), cut, refinement_cause, "SPARSE_BOUNDARY_BRACKET"))
            elif decision == "unknown":
                boundaries.append({"time": candidate.time, "response": response,
                                   "candidate_id": candidate.candidate_id})
            self.ledger.append(record)
        # Never merge new_unit cuts just because their classes happen to match.
        grouped = {}
        for boundary in boundaries:
            grouped.setdefault(boundary["time"], []).append(boundary)
        cuts = [0] + sorted(grouped) + [source.duration]
        spans = []
        for index, (start, end) in enumerate(zip(cuts, cuts[1:])):
            claims, causes = [], []
            for boundary in grouped.get(start, []):
                response = boundary["response"]
                if response["decision"] == "unknown":
                    causes.append(response["cause"])
                else:
                    claims.append(response["right"])
            for boundary in grouped.get(end, []):
                response = boundary["response"]
                if response["decision"] == "unknown":
                    causes.append(response["cause"])
                else:
                    claims.append(response["left"])
            for candidate, response in judged:
                if start < candidate.time < end and response["decision"] == "same_unit":
                    claims.extend([response["left"], response["right"]])
            review = self.ask(source, "u%06d" % index, "unit_review", start, end, (start+end)/2,
                              {"neighbor_claims": claims})
            if review["decision"] == "unknown":
                causes.append(review["cause"])
                unit = None
            else:
                unit = review["unit"]
                claims.append(unit)
            identities = {c["unit_id"] for c in claims}
            classes = {c["type"] for c in claims if c["type"] != "unknown"}
            conflict = len(identities) > 1 or len(classes) > 1
            if conflict:
                causes.append("semantic")
                self.uncertainties.append({"source_id": source.source_id, "interval": [start, end],
                                           "code": "NEIGHBOR_IDENTITY_OR_CLASS_CONFLICT"})
            if unit is None or unit["type"] == "unknown":
                causes.append("semantic")
                if unit is not None:
                    self.uncertainties.append({"source_id": source.source_id, "interval": [start, end],
                                               "code": "UNIT_CLASS_UNKNOWN"})
            kind = "unknown" if causes else unit["type"]
            cause = ("technical" if "technical" in causes else "semantic") if causes else None
            spans.append({"start": start, "end": end, "type": kind, "cause": cause,
                          "unit_id": unit["unit_id"] if unit else None,
                          "group": index})
            self.ledger.append({"source_id": source.source_id, "event": "reconciled_unit",
                                "interval": [start, end], "type": kind, "unknown_cause": cause,
                                "unit_id": unit["unit_id"] if unit else None,
                                "consistent_neighbor_claims": not conflict})
        # Overlay sparse timing uncertainty without changing retained content-unit cuts.
        endpoints = sorted({0, source.duration, *(s["start"] for s in spans),
                            *(s["end"] for s in spans), *(a for a, _, _, _ in ambiguity),
                            *(b for _, b, _, _ in ambiguity)})
        pieces = []
        for start, end in zip(endpoints, endpoints[1:]):
            span = next(s for s in spans if s["start"] <= start and s["end"] >= end)
            piece = {**span, "start": start, "end": end}
            overlapping = [cause for a, b, cause, _ in ambiguity if a < end and b > start]
            if overlapping:
                piece["type"] = "unknown"
                piece["cause"] = "technical" if span["cause"] == "technical" or "technical" in overlapping else "semantic"
            # Coalesce only pieces of the SAME reconciled interval, never adjacent units.
            if (pieces and pieces[-1]["group"] == piece["group"] and
                    pieces[-1]["type"] == piece["type"] and pieces[-1]["cause"] == piece["cause"]):
                pieces[-1]["end"] = end
            else:
                pieces.append(piece)
        for start, end, cause, code in ambiguity:
            self.uncertainties.append({"source_id": source.source_id, "interval": [start, end], "code": code})
        return pieces

    def refine(self, source, candidate_id, response):
        """Ask for successively narrower frame pairs when a native decode exists."""
        frames = {f.frame_id: f for f in source.frames}
        cause, stopping = "semantic", "sparse_input_no_native_proof"
        for step in range(self.config.max_refinement_steps):
            pair = response["boundary"]
            old, new = frames[pair["last_old_frame_id"]], frames[pair["first_new_frame_id"]]
            if old.native_sequence is None or old.native_sequence != new.native_sequence:
                break
            if new.native_index == old.native_index + 1:
                stopping = "native_neighbors_verified"
                break
            refined = self.ask(source, candidate_id + ":refine:%d" % step, "boundary_refinement",
                               max(0, old.time), new.time, (max(0, old.time)+new.time)/2,
                               {"refinement_bracket": [old.time, new.time],
                                "supported_left_identity": response["left"],
                                "supported_right_identity": response["right"],
                                "instruction": "Locate final old and first new unit frames INSIDE this bracket, retaining supported identities."})
            if refined["decision"] == "unknown":
                cause = refined["cause"]
                stopping = "native_refinement_" + cause + "_unknown"
                break
            if refined["left"] != response["left"] or refined["right"] != response["right"]:
                stopping = "native_refinement_identity_conflict"
                self.uncertainties.append({"source_id": source.source_id, "request_id": candidate_id,
                                           "code": "NATIVE_REFINEMENT_IDENTITY_CONFLICT"})
                break
            if refined["boundary"] == pair:
                stopping = "native_refinement_no_progress"
                break
            response = refined
        else:
            stopping = "native_refinement_step_limit"
        # Check the last accepted pair even if the step budget just ran out.
        pair = response["boundary"]
        old, new = frames[pair["last_old_frame_id"]], frames[pair["first_new_frame_id"]]
        if (old.native_sequence is not None and old.native_sequence == new.native_sequence
                and new.native_index == old.native_index + 1):
            stopping = "native_neighbors_verified"
        self.ledger.append({"source_id": source.source_id, "request_id": candidate_id,
                            "event": "boundary_refinement_stop", "stopping_reason": stopping,
                            "last_old": old.ref(), "first_new": new.ref()})
        return response, cause


_SAFE_ERRORS = {"NO_SEMANTIC_PROVIDER_CONFIGURED", "REPLAY_REQUEST_NOT_FOUND", "REPLAY_RECORDED_FAILURE",
                "HTTP_BUDGET_EXHAUSTED", "HTTP_TRANSPORT_FAILURE", "HTTP_MALFORMED_RESPONSE",
                "HTTP_RESPONSE_TOO_LARGE", "HTTP_RETRY_EXHAUSTED", "UNSUPPORTED_MULTIMODAL_IMAGE",
                "HTTP_REPORTED_COST_EXCEEDED_RESERVATION"}


def run(manifest, output, provider=None, config=None):
    """Run once into a NEW directory; return summary. Fatal input errors create no run.

    Source/evidence/provider failures remain full-coverage unknown predictions.
    Custom provider exceptions are isolated and never serialized verbatim.
    """
    config = config or Config()
    config.validate()
    provider = provider or OfflineProvider()
    sources, synthetic = read_manifest(manifest)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    engine = Engine(provider, config)
    cases, inventories, warnings, source_summaries = {}, {}, [], []
    for index, source in enumerate(sources):
        folder = output / ("source_%04d" % index)
        folder.mkdir()
        ingest(source)
        if not source.errors and not source.frames and source.data.get("video_path"):
            try:
                decode_video(source, folder / "decoded", config.max_context,
                             config.max_native_frames, config.media_timeout)
            except Exception as exc:
                # Known media codes only. Neither file paths nor tool stderr are copied.
                code = str(exc).split(":", 1)[0]
                if code not in {"MEDIA_TOOLS_MISSING", "FFPROBE_FAILED", "FFMPEG_DECODE_FAILED",
                                "NATIVE_FRAME_PTS_MISSING", "NATIVE_PTS_NOT_STRICTLY_INCREASING",
                                "NO_VIDEO_FRAMES_IN_WINDOW", "NATIVE_FRAME_LIMIT_EXCEEDED",
                                "NATIVE_FRAME_COUNT_MISMATCH", "VIDEO_CHANGED_DURING_DECODE"}:
                    code = "MEDIA_DECODE_FAILURE"
                source.errors.append({"code": code})
        if not source.frames or not any(0 <= f.time < source.duration for f in source.frames):
            source.errors.append({"code": "NO_VALID_WINDOW_FRAMES"})
        pieces = None
        if not source.errors:
            try:
                pieces = engine.segment(source)
            except (ValueError, OSError):
                source.errors.append({"code": "CANDIDATE_OR_EVIDENCE_PROCESSING_FAILED"})
        if source.errors:
            for error in source.errors:
                engine.failures.append({"source_id": source.source_id, **error})
            pieces = [{"start": 0, "end": source.duration, "type": "unknown", "cause": "technical"}]
        raw, floor, causes = [], [], {}
        for number_, piece in enumerate(pieces):
            sid = "seg_" + digest({"source": source.source_id, "index": number_,
                                   "start": piece["start"], "end": piece["end"]})[:20]
            row = {"source_id": source.source_id, "segment_id": sid, "start": piece["start"],
                   "end": piece["end"], "type": piece["type"]}
            raw.append(row)
            quantized = {**row, "start": math.floor(row["start"]), "end": math.floor(row["end"])}
            floor.append(quantized)
            if row["type"] == "unknown":
                causes[sid] = piece["cause"]
            if quantized["start"] == quantized["end"]:
                warnings.append({"source_id": source.source_id, "segment_id": sid,
                                 "code": "QUANTIZATION_ZERO_LENGTH", "scorer_compatibility": "not_assumed"})
            engine.ledger.append({"source_id": source.source_id, "event": "output_segment",
                                  "segment_id": sid, "unit_identity": piece.get("unit_id"),
                                  "unknown_cause": piece.get("cause")})
        if math.floor(source.duration) != source.duration:
            warnings.append({"source_id": source.source_id, "code": "QUANTIZATION_DURATION"})
        write_jsonl(folder / "raw.jsonl", raw)
        write_jsonl(folder / "floor.jsonl", floor)
        case = {"unknown_causes": causes}
        for kind in ("raw", "floor"):
            path = folder / (kind + ".jsonl")
            case[kind] = {"path": path.relative_to(output).as_posix(), "sha256": file_hash(path)}
        cases[source.source_id] = case
        inventories[source.source_id] = {"evidence": source.asset_hashes,
                                        "asr_sha256": digest(source.asr), "coarse_sha256": digest(source.coarse),
                                        "window_start_pts_us": source.start_pts_us}
        source_summaries.append({"source_id": source.source_id, "channel": source.channel,
                                 "duration": source.duration, "rows": len(raw), "unknown_rows": len(causes),
                                 "technical_source_failure": bool(source.errors)})
    synthetic_only = synthetic
    candidate = {"schema": "local-candidate-evaluation-v1", "candidate_id": "portable_auto_v1",
                 "origin": "synthetic_contract_fixture" if synthetic_only else "unverified_model_output",
                 "provenance_note": "Actual provider/evidence; no private-set claims",
                 "cases": cases}
    write_json(output / "candidate_manifest.json", candidate)
    write_jsonl(output / "decisions.jsonl", engine.ledger)
    write_jsonl(output / "response_receipts.jsonl", engine.receipts)
    http_provider = provider if isinstance(provider, HTTPProvider) else None
    write_jsonl(output / "http_attempt_receipts.jsonl", http_provider.receipts if http_provider else [])
    write_json(output / "configuration.json", {"algorithm": asdict(config), "provider": provider.name,
               "replay": provider.name == "replay", "synthetic": synthetic_only,
               "provider_config": _public_provider_config(provider), "policy": POLICY})
    write_json(output / "failure_report.json", {"technical_failures": engine.failures,
               "semantic_uncertainties": engine.uncertainties, "quantization_warnings": warnings})
    cost_mode = "http_receipts" if http_provider else "none" if getattr(provider, "cost_mode", None) == "none" else "unreported"
    unreported_cost = cost_mode == "unreported"
    summary = {"status": "completed_with_technical_failures" if engine.failures else "completed",
               "provider": provider.name, "replay": provider.name == "replay", "synthetic": synthetic_only,
               "provider_declared_synthetic": getattr(provider, "synthetic", False),
               "runtime_seconds": time.monotonic() - started, "semantic_invocations": engine.semantic_calls,
               "http_attempts": http_provider.calls if http_provider else 0,
               "known_cost": http_provider.known_cost if http_provider else None if unreported_cost else 0,
               "unknown_cost_attempts": http_provider.unknown_cost_attempts if http_provider else 0,
               "unknown_cost_invocations": engine.semantic_calls if unreported_cost else 0,
               "cost_scope": "unreported_injected_provider" if unreported_cost else cost_mode,
               "cost_charged_or_reserved": http_provider.charged_or_reserved if http_provider else None if unreported_cost else 0,
               "technical_failure_count": len(engine.failures), "sources": source_summaries,
               "private_metrics": "NOT EVALUATED", "held_out_metrics": "NOT EVALUATED",
               "endpoint_v2": {"status": "NOT EVALUATED", "grouping": ["channel", "class"],
                               "type_correctness_target": 0.95, "split_correctness_target": 0.80,
                               "endpoint_tolerance_seconds": 1,
                               "representations": ["raw", "floor"],
                               "boundary_families": ["ad_program", "same_class_unit"],
                               "denominators": "NOT EVALUATED"}}
    write_json(output / "summary.json", summary)
    hashes = {p.relative_to(output).as_posix(): file_hash(p)
              for p in sorted(output.rglob("*")) if p.is_file()}
    write_json(output / "hashes.json", {"input_manifest_sha256": file_hash(manifest),
               "config_sha256": digest(asdict(config)), "provider_replay_sha256": provider.replay_hash if isinstance(provider, ReplayProvider) else None,
               "sources": inventories, "outputs": hashes,
               "note": "Content hashes bind bytes, not semantic correctness. This index excludes itself."})
    return summary


def _public_provider_config(provider):
    if not isinstance(provider, HTTPProvider):
        return {}
    cfg = provider.config
    # Endpoint/model/credential variable names can contain private operator values;
    # bind configuration without disclosing them.
    return {"endpoint_sha256": digest(cfg.endpoint), "model_sha256": digest(cfg.model),
            **{key: getattr(cfg, key) for key in ("max_calls", "max_cost", "max_request_cost",
                                                "max_retries", "timeout", "max_tokens", "wire_format")}}
