# Execution record

Only generated synthetic inputs and localhost HTTP fixtures were used. No private project, broadcast video, transcript, GT, model account or credential was accessed. No paid calls occurred. Private-set and held-out metrics: **NOT EVALUATED**.

## Commands actually run

```sh
python3 -B -m unittest discover -s tests -v
python3 -B -m adseg_auto.fixture --output test/scratch/final_fixture
python3 -B -m adseg_auto --manifest test/scratch/final_fixture/manifest.json --output test/scratch/final_run --provider replay --replay test/scratch/final_fixture/replay.json
python3 -B -m adseg_auto.verify --input-manifest test/scratch/final_fixture/manifest.json --candidate-manifest test/scratch/final_run/candidate_manifest.json
python3 -B test/scratch/reference/verify_artifacts.py --input-manifest test/scratch/final_fixture/manifest.json --candidate-manifest test/scratch/final_run/candidate_manifest.json
python3 -B -m unittest discover -s test/scratch/reference -p 'test_*.py' -v
```

The reference verifier and its tests were read from the [public artifact-check repository](https://github.com/BENZEMA216/adseg-auto/tree/main/acceptance) into scratch. They are not a project dependency or a bundled certification. Scratch is intentionally excluded from submission. The independently implemented `adseg_auto.verify` command remains available offline in the delivered source.

Final project suite: **40 tests passed**, exit `0`, approximately 5.0 seconds. It covers required business cases, malformed answers and exceptions, context expansion, unknown preservation, floor correspondence, real file hashes, stable IDs, privacy of error logs, no overwrite, native PTS mapping/refinement, dependency-free wheel loading, HTTP budget/retry/redaction behavior, and unreported injected-provider costs without copying private provider internals. HTTP tests used only a temporary localhost server. Media subprocess tests used synthetic ffprobe/ffmpeg fixtures and checked literal argument arrays and variable timestamp mapping.

The fresh fixture generator exited `0`, authoring 27 explicit semantic replay responses for three sources. The segmentation command exited **`1` as expected**, after writing complete artifacts: its deliberately empty-frame source is a technical failure. Both local and public artifact verifiers exited `0`, with no structural errors and 10 retained quantization warnings. The reference verifier's own separate suite also passed its 16 tests; those are tests of the checker, not additional algorithm accuracy evidence.

| Synthetic source | Raw rows | Unknown rows | Outcome |
|---|---:|---:|---|
| `synthetic_tv` | 15 | 7 | Eight independent content units retained; seven sparse timing brackets stay unknown. |
| `synthetic_uncertain` | 1 | 1 | Semantic unknown, full duration retained. |
| `synthetic_missing` | 1 | 1 | Technical unknown, full duration retained. |

There were zero HTTP attempts, zero live charges, one intentional technical source failure, seven retained zero-duration floor rows and three fractional-duration warnings. No failure or short row was removed to make the check pass.

## Final built-wheel smoke run

After the final code changes, the project suite was rerun: **40/40 passed**. A `python3 -B` standard-library script called `adseg_build.build_wheel`, extracted the wheel into a fresh scratch directory, and launched these actual module arguments with that extracted directory as the working directory:

```sh
python3 -B -m adseg_auto.fixture --output fixture
python3 -B -m adseg_auto --manifest fixture/manifest.json --output run --provider replay --replay fixture/replay.json
python3 -B -m adseg_auto.verify --input-manifest fixture/manifest.json --candidate-manifest run/candidate_manifest.json
```

Observed exits were `0`, `1` (intentional missing-frame fixture), and `0`. This run used the built package outside the repository source tree. It again produced 17 rows over all three sources, nine unknown rows, one technical failure, 10 quantization warnings, 27 semantic invocations and zero HTTP attempts. A byte scan confirmed that its artifacts contained no workspace absolute path.

The public verifier was also actually run against this final wheel-created demo:

```sh
python3 -B test/scratch/reference/verify_artifacts.py --input-manifest test/scratch/wheel_smoke_v2/installed/fixture/manifest.json --candidate-manifest test/scratch/wheel_smoke_v2/installed/run/candidate_manifest.json
```

It exited `0`, reporting `passed`, no errors and the same 10 warnings. The final synthetic manifests had these hashes:

```text
input manifest:     4b85cd6d9f227937b5c794adc2730a5190d4b6a42de0e412d4bb80e76b3d1278
candidate manifest: d48cef5f16361c05cc8e744e1350b99ea33d2f9adb63b12ed1940a0d6eed380e
```

These hashes bind fixture bytes, not semantic correctness. Runtime-bearing receipts and summaries naturally differ between executions.

## Practical limits and integration

`python3 -B -m pip --version` answered `No module named pip`; no pip installation result is claimed. The supplied dependency-free backend built a wheel successfully and the extracted wheel completed the demo. Running from a clone needs no installer. `ffmpeg` and `ffprobe` were unavailable; actual media decoding was not run. The absent-tool path and native mapping/refinement logic were tested with synthetic fixtures. No real external HTTP endpoint, billing account or trained broadcast model was tested.

To integrate, adapt local evidence into the documented manifest, implement `Provider.judge` or configure the opt-in multimodal HTTP adapter, and invoke `run` into a new output directory. Verify file structure locally, then pass unchanged raw/floor predictions and failure reports to the requester's frozen scorer. Retain unknowns and all source denominators. Report raw/floor, channel/class and the two boundary families separately. Accuracy targets and all private/held-out results remain **NOT EVALUATED**.

The source bundle is prepared for IMD publication to the assigned GitHub repository. No manual push, alternate repository, service or contract deployment was attempted.
