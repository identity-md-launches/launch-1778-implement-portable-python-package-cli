# adseg-auto

A portable Python 3.10+ package for proposing and adjudicating independent Chinese television broadcast units. It produces auditable `ad`, `program` and `unknown` intervals. The core, tests and synthetic demo use only the Python standard library. No dependency download, GPU, media binary, model account or private dataset is needed for the demo.

This is an independent local integration component. Private videos, code, transcripts, ground truth, predictions and credentials were neither accessed nor tested. Private-set and held-out endpoint-v2 accuracy: **NOT EVALUATED**. Synthetic replay tests exercise behavior and file contracts; they do not demonstrate model accuracy or blind automation.

## Run offline

From the repository root:

```sh
python3 -B -m unittest discover -s tests -v
python3 -B -m adseg_auto.fixture --output demo_fixture
python3 -B -m adseg_auto --manifest demo_fixture/manifest.json --output demo_run --provider replay --replay demo_fixture/replay.json
python3 -B -m adseg_auto.verify --input-manifest demo_fixture/manifest.json --candidate-manifest demo_run/candidate_manifest.json
```

Use new directory names each time. Existing outputs, including empty directories, are refused. The demo deliberately includes a missing-frame source: the segmentation command returns **1 after writing all outputs**, while verification returns **0**. The three sources demonstrate a storyboard, semantic uncertainty and technical failure. The storyboard includes consecutive same-brand ads, shots within an ad, a 340 ms channel promotion, independent trailers, a public-service ad, sponsorship within a program, a shopping program, variable PTS and outside-window context. It is authored synthetic evidence with explicit replay responses.

CLI exit codes: `0` completed with no technical failures (semantic unknowns may remain); `1` completed with technical failures preserved in predictions and reports; `2` invalid invocation, manifest identity, configuration or unavailable output. A source with broken evidence still gets full coverage. A manifest without valid source IDs, channels and positive finite durations is rejected before creating a run: its scored intervals cannot be defined.

No calls are paid by default. Omitting `--provider` uses `offline`, which writes technical unknowns rather than inferring semantics from image changes. Replay is explicitly selected and never contacts a service. Tests for HTTP use an in-process localhost server only.

## Input and coordinates

The manifest is UTF-8 JSON: either `{"sources": [...]}` or a nonempty source array. A top-level `"synthetic": true` declares that **all** supplied sources are synthetic. Each source has:

| Field | Meaning |
|---|---|
| `source_id`, `channel` | Nonempty strings; source IDs are unique. IDs are never used as filesystem paths. |
| `duration` | Positive finite seconds of the scored window. |
| `window_start_pts_us` | Integer source-media PTS microseconds; defaults to `0`. |
| `frames` | Optional ordered or unordered list of `{frame_id, pts_us, path, sha256}`. Frame IDs and PTS are unique across window and context frames. File hashes must match. |
| `asr` | Optional list of `{start, end, text}`, with positive-length intervals in window-relative seconds. |
| `coarse_segments` | Optional `{segment_id, start, end, type}` hints, in window-relative seconds. They never establish class or identity. |
| `context` | Optional object with `frames` and/or `asr` in the same coordinates as above. It can extend outside the window. |
| `video_path` | Optional local video, decoded only when pre-extracted frames are absent. |

Paths resolve against the manifest directory, independently of the working directory. The inference engine opens only the explicitly supplied evidence files. It never discovers private files or loads storyboard/GT/score/answer fields. ASR, coarse labels, frame text and provider responses are evidence, not executable instructions. JSON with duplicate keys, non-finite values or boolean times is rejected. A hash mismatch or malformed source evidence fails that source conservatively.

Each frame's time is computed **once** as `(pts_us - window_start_pts_us) / 1_000_000`. ASR/coarse times are already window-relative and receive no PTS offset. Outputs cover exactly `[0, duration]`; outside context never extends that interval. Evidence receipts keep the original PTS and calculated time. The synthetic fixture uses a nonzero PTS origin to test this distinction.

Pre-extracted frames alone carry no trusted native adjacency proof. Supplying an FPS, `native_index`, or similar extra field cannot turn them into native evidence. For native decoding, supply a video-only source. Do not specify a video merely as a hint when expecting the pre-extracted branch to decode it as well.

## Algorithm and uncertainty

1. Propose candidates at regular probes throughout the window, every ASR/coarse edge, and image changes, including inside coarse units. The PPM/PNG descriptor compares a small spatial RGB grid. Unsupported image encodings use changed file hashes as a conservative proposal cue. These cues never decide class. No minimum-duration rule removes short units; resource limits fail explicitly instead of silently pruning candidates.
2. Ask the injectable semantic provider about **every** candidate. Requests include ordered before/during/after frames, local focal neighbors, sampled context across the full focal interval, ASR and coarse hints. Responses establish `same_unit`, `new_unit` or `unknown`, content-instance identities, class and evidence citations. Insufficient context expands geometrically within configured bounds. Stop reasons include supported decision, exhausted available evidence, maximum context/expansions and technical failure. Duplicate evidence at one expansion does not prevent reaching farther evidence within the allowed bounds.
3. For a supported boundary, bind the final old-unit and first new-unit frames to actual IDs/PTS/hashes. Native decoded frames must have adjacent verified stream indices. Nonadjacent native pairs receive bounded, successively narrower semantic refinement requests. No FPS-derived times, constant offsets, second snapping or invented subframe precision are used.
4. Remove supported `same_unit` shot cuts. Keep independent `new_unit` cuts even when both sides have the same class or brand. Review each resulting **whole interval** and reconcile its identity/class with all neighboring and internal claims. Contradictions become semantic unknowns. A new decision with identical identities, a same decision with inconsistent claims, fabricated frame IDs or an invalid boundary pair is a malformed answer and therefore a technical failure.
5. Unknown candidate decisions remain explicit provisional divisions and make their adjoining intervals unknown. A sparse boundary's interval from the observed old frame to the observed new frame becomes **semantic unknown**; its `first_new` PTS is a supported observation, not a claim that the native cut occurred there. Sparse timing brackets and distinct content cuts remain in the ledger. A failed native refinement makes its unresolved bracket technical unknown. All sources and failed decisions remain in the reports and denominators.

The semantic policy treats independently broadcast ads, including public-service ads, as `ad`. Normal programs, shopping programs, standalone trailers and channel promotions are `program`, with distinct units separated within that class. Program packaging, intro/outro and integrated sponsorship remain part of their program unless evidence supports an independent insertion. Price, QR code, product, presenter or logo alone never suffices. The policy text is saved with every run.

The operator invokes the pipeline to obtain local predictions and pays any configured HTTP costs. The pipeline drives proposals, context expansion, refinement and output writing; no daemon, service or external scheduler is required. Replay exercises recorded decisions without a model. Its synthetic authoring provider exists only in the fixture generator, not as a selectable inference mode.

## Providers and local integration

```python
from adseg_auto import Config, run

class LocalSemanticProvider:
    name = "my_local_multimodal_model"
    synthetic = False
    cost_mode = "none"  # Declare this only for a locally free implementation.

    def judge(self, request, frames):
        # Implement inference here. frames contain local paths plus real ID/PTS/hash.
        # Return the response schema below; do not send GT or scorer outputs.
        return {
            "decision": "unknown",
            "evidence_frame_ids": [],
            "reason": "The local model has insufficient independent-content evidence."
        }

run("input.json", "new_run", LocalSemanticProvider(), Config())
```

`Provider` is a typing protocol in `adseg_auto.providers`. `judge(request, frames)` returns a JSON-compatible object or raises `ProviderError(code)` for technical failure. Other exceptions are isolated; exception text is not logged. The semantic request schema is `adseg-semantic-request-v1`; its `stage` is `candidate`, `unit_review` or `boundary_refinement`. `focal_interval`, `left_interval`/`right_interval`, `focus`, evidence, context bounds and ordered frame references are explicit. `adseg_auto.providers.POLICY` contains the complete provider instruction. Evidence sampling always retains focal neighbors and interval endpoints, and records available/selected counts; it is not an assertion that every frame was sent to the model.

Injected providers must manage any network permissions, spending cap and retries themselves. Costs are unreported by default: the summary records unknown-cost invocations and null monetary totals, rather than assuming that an arbitrary provider was free. A provider may explicitly declare `cost_mode = "none"` when it makes no paid calls. The concrete HTTP adapter instead reports and reserves each HTTP attempt; its budget is not automatically applied to custom provider code.

A candidate response has this structure (IDs below refer to actual supplied frames and content instances):

```text
{
  "decision": "same_unit" | "new_unit" | "unknown",
  "left":  {"unit_id": content_instance_id, "type": "ad" | "program" | "unknown"},
  "right": {"unit_id": content_instance_id, "type": "ad" | "program" | "unknown"},
  "evidence_frame_ids": [actual_frame_ids],
  "reason": explanation,
  "boundary": {"last_old_frame_id": actual_id, "first_new_frame_id": actual_id}
}
```

`new_unit` requires distinct content-instance IDs and a cited, ordered boundary pair. `same_unit` requires identical left/right identity and class and omits `boundary`. `unknown` may omit identities/boundary but must retain a reason and an evidence list (possibly empty). Supported decisions require cited frame evidence. A whole-unit review returns `decision: same_unit`, `unit: {unit_id,type}`, evidence IDs and reason, or `unknown` when the interval is ambiguous or contains multiple units. Refinement retains the already supported left/right identities and returns a narrower `new_unit` pair or `unknown`. Providers must choose unknown when evidence cannot establish the claimed relationship.

Explicit replay files use `{"schema":"adseg-replay-v1","synthetic":true|false,"responses":{REQUEST_SHA256: RESPONSE}}`. A response may instead be `{"error": ...}` to replay a technical failure. The request digest is SHA256 of UTF-8, sorted-key compact JSON (`adseg_auto.util.digest`). It includes evidence hashes, policy hash, timing, source/channel and context; paths are excluded. Changing evidence/configuration can invalidate a recording. Missing entries become technical unknowns; replay never guesses a nearest matching answer. Every replay result is marked in configuration, receipts and summary.

### Opt-in multimodal HTTP

The operator creates an HTTP configuration JSON using the fields of `HTTPConfig`:

| Required setting | Meaning |
|---|---|
| `endpoint` | Operator's HTTPS inference URL; credentials, queries and fragments in URLs are refused. |
| `model` | Operator-selected multimodal model. |
| `credential_env` | Name of an environment variable containing the operator's bearer credential. Its value never belongs in JSON, source or CLI arguments. |
| `max_calls` | Positive cap including retries. |
| `max_cost` | Positive total budget in the endpoint's configured currency. |
| `max_request_cost` | Operator's conservative upper bound reserved **before every attempt**, including failed attempts. |

Optional settings: `max_retries` (default 1, allowed 0–3), `timeout` (30 seconds), `max_tokens` (1500), `wire_format` (`chat_completions` or `json`). Insecure HTTP is only allowed for localhost with explicit `allow_insecure_localhost: true`; tests use that option. Redirects are refused so credentials cannot be forwarded to another host.

```sh
python3 -B -m adseg_auto --manifest input.json --output new_http_run --provider http --http-config operator_http.json
```

`chat_completions` posts system policy and user text plus ordered base64 `image_url` content, using the configured model, token limit and JSON response format. It expects `choices[0].message.content` containing the semantic JSON object. `json` posts `{model,max_tokens,policy,request,multimodal_content}` and expects `{response: SEMANTIC_OBJECT}`. Neither format requires a particular vendor. PPM frames are encoded as PNG for HTTP; PNG/JPEG/WebP/GIF can be transmitted directly. PNG and PPM image-change descriptors are decoded locally without a library.

An optional outer `usage.cost` is recorded as **server-reported**, not independently billed cost. Missing costs (including 429, transport and malformed responses) stay unknown and retain their reserved amount. Unknown costs are never treated as free. Retryable failures are 429, 5xx and transport errors; retries, delays, timeouts and total attempts are bounded. A server-reported cost above its reservation stops that semantic request and can exhaust further calls. A client cannot enforce a remote provider's billing: configure a real provider-side spending cap and a valid worst-case reservation. Model calls can be numerous because candidates, whole-unit reviews, context expansions and native refinements all consume the budget.

The HTTP adapter saves only whitelisted semantic response fields with the configured credential redacted. Invalid bodies and HTTP errors retain hashes, byte counts, status, timing and unknown costs, without raw body text or server exception strings. Endpoint/model configuration is bound by hashes; credential values and environment snapshots are never saved. Evidence and transcripts sent to the configured endpoint remain subject to that operator's data policy. Offline/replay do not transmit them.

## Optional video decoding

Install `ffmpeg` and `ffprobe` yourself only if video decoding is wanted; they are not dependencies of this project or demo. Missing binaries produce `MEDIA_TOOLS_MISSING` in the failure report and a full technical unknown source. This delivery's media adapter was exercised with synthetic subprocess fixtures, not real broadcast files.

The adapter probes the selected video stream's integer PTS and rational time base, converts them to integer microseconds once, and decodes all native frames in the scored/context range to 320-pixel-wide evidence thumbnails. `ffmpeg` selection uses stream frame indices and preserves presentation order; output count and increasing PTS are checked. Hashes bind the video, decoded evidence, stream/time base and native sequence. Original rational PTS ticks, microsecond rounding and indices are retained in `decoded/pts_index.json` and frame references. Actual PTS spacing can vary. Missing/duplicate/non-increasing PTS, video mutation, decode failures, limits and timeouts cause explicit technical failure. All subprocess calls use argument arrays and `shell=False` semantics; paths containing spaces or shell punctuation remain literal arguments. No host-specific paths are embedded in source or artifacts.

For long sources, probing decodes metadata across the file and can be expensive. Configure `max_native_frames` and `media_timeout`, or supply pre-extracted evidence. Native temporal adjacency of thumbnails proves the index relationship in that decode; it does not prove that a provider labeled the content correctly.

## Outputs and evaluation handoff

Every source, including failed sources, has `source_NNNN/raw.jsonl` and `floor.jsonl`. Each raw row has exactly `{source_id,segment_id,start,end,type}`. Raw intervals are positive, finite, ordered and cover `[0,duration]` without gaps/overlaps. IDs are deterministic from source ID, row index and raw endpoints. Floor retains the exact same row order, IDs and types, with **every** endpoint passed through `math.floor`. Its coverage is `[0,floor(duration)]`. Zero-duration floor rows stay present and are reported as `QUANTIZATION_ZERO_LENGTH`; fractional total duration is reported as `QUANTIZATION_DURATION`. Do not remove these rows to satisfy another scorer.

`candidate_manifest.json` uses `local-candidate-evaluation-v1`, `portable_auto_v1`, portable relative raw/floor paths and actual SHA256 values. Origin is `synthetic_contract_fixture` for a declared synthetic-only manifest, otherwise `unverified_model_output`. Each case's `unknown_causes` maps exactly its unknown row IDs to `technical` or `semantic`.

| Artifact | Contents |
|---|---|
| `decisions.jsonl` | Every semantic request/attempt, evidence references, candidate cues and decision, context/refinement stopping reasons, native/sparse boundary status, unit reconciliation, output-ID links. |
| `response_receipts.jsonl` | Sanitized response, request/response hashes, provider, runtime and technical outcome for each semantic invocation. |
| `http_attempt_receipts.jsonl` | Per-attempt HTTP statuses (including 429), body hashes, sanitized successful fields, retries, runtime, known/unknown cost and reservations. Empty for offline/replay. |
| `configuration.json` | Algorithm settings, policy, provider/replay/synthetic markers and sanitized provider configuration. |
| `hashes.json` | Manifest/config/replay hashes, evidence inventory, ASR/coarse hashes and all other output hashes. The hash index excludes itself. |
| `summary.json` | Runtime, source/row/failure counts, invocations, attempts, known/unknown/reserved costs and explicit unevaluated metrics. |
| `failure_report.json` | Technical failures, semantic uncertainty intervals, conflicts and quantization incompatibilities. |

Hashes establish byte identity, not provider authenticity or semantic correctness. The local verifier checks file contracts only; no verifier result establishes segmentation accuracy. Keep evidence/receipt outputs local when using private data.

The requester's frozen endpoint-v2 evaluation remains local and **NOT EVALUATED** here. Preserve the original source set, failed cases, GT, tolerance and denominators. Evaluate **raw and floor separately**, **per channel and class**, separating **ad↔program** boundaries from **same-class independent-unit** boundaries. Targets are type correctness ≥95%, split correctness ≥80%, endpoint tolerance ≤1 second. This package supplies no replacement scorer, invented denominator or synthetic accuracy claim.

## Configuration, packaging and limits

`--config algorithm.json` accepts `Config` fields. Defaults: probes every 2 s, image change threshold 0.12, context radius 2–16 s with at most three expansions, 48 evidence frames per request, 10,000 candidates, 120,000 native frames, 180 s per media subprocess and four native refinement steps. Candidate and media limits produce full technical unknowns rather than dropping parts of a source. No minimum unit duration is enforced.

No image heuristic establishes independent content. There is no bundled trained Chinese broadcast classifier or offline semantic model. Useful non-replay classifications require a real injected provider or configured HTTP model; their quality has not been validated here. Sparse frames, low contrast/hidden insertions, missing transcripts, sampling and provider mistakes can miss units. Whole-unit reviews that cannot establish a single consistent identity remain unknown. Conservative unknown brackets can increase row counts and affect external scoring. This is deliberate uncertainty reporting, not a claim of reaching the acceptance targets.

The package runs directly from a clone. It also has a dependency-free PEP 517 wheel backend; with an existing pip installation, `python3 -m pip install --no-index --no-build-isolation .` needs no download. Or build an ordinary pure-Python wheel using the standard library:

```sh
mkdir -p dist
python3 -B -c "import adseg_build; print(adseg_build.build_wheel('dist'))"
```

Source is prepared for publication in the [assigned repository](https://github.com/identity-md-launches/launch-1778-implement-portable-python-package-cli) through IMD's code-bundle delivery. No service, smart contract, alternate repository or manual credential-dependent push is required. [CHECKS.md](CHECKS.md) records commands actually run and observed results. The [public specification](https://github.com/BENZEMA216/adseg-auto) describes the independent artifact checks; it does not certify this algorithm.
