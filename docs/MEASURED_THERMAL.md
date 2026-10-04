# Thermal estimation from retained recordings

`measured-thermal` / `ciw.measured-thermal.v1` connects four retained observation
streams to the existing Python two-capacity thermal reference. It uses the same
Workbench source, operation, result, correction and replay lifecycle. It does not
open a device, apply calibration, fit parameters or launch Julia.

The original `thermal-observer` synthetic source and runtime remain unchanged.

On Windows, use a short checkout, build and results path. Qualification encountered
legacy path-length limits in packaging and retained-workspace writes; the same
source passed from shorter paths. Long-path support remains an open host gate.

## Run, inspect and replay

Start with a retained workspace, then supply a recording source:

```sh
net demo --output-dir results/context
net science run --workspace results/context/workspace.json \
  --kind measured-thermal --source examples/measured-thermal/source.json \
  --label "Thermal ingress software fixture" --output-dir results/thermal --json
net science inspect --workspace results/thermal/workspace.json \
  --bundle '<bundle_id returned by run>' --json
net science replay --workspace results/thermal/workspace.json \
  --bundle '<bundle_id returned by run>' --output-dir results/thermal-replay --json
```

The supplied example is **test-fixture data**, not a physical measurement or
calibration certificate. It exercises the recording interface without hardware.
For an actual study use an operator-prepared source with `origin: measured` and
retained measurement and calibration records. A declaration does not authenticate
an instrument or establish its accuracy.

Reopen validates retained bytes and bounded numerical consistency without creating
an execution. Explicit replay requires the exact recorded runtime identity and
creates fresh execution, result and bundle identities. Runtime identity covers
the adapter, reused kernel, observation/comparison/covariance validation helpers
and NumPy version. Offline numerical checks use the existing binary64 tolerances;
replay requires identical retained numerical content. It reprocesses the same
recording; it does not repeat the physical experiment.

## Recording contract

The source schema is `ciw.measured-thermal-source.v1`:

| Field | Required meaning |
| --- | --- |
| `experiment_id`, `asset_id`, `configuration_id` | Explicit investigation, apparatus and installed-configuration declarations. |
| `origin` | `measured` uses observed semantics; `test_fixture` uses simulated semantics throughout the four input streams. |
| `clock` | Clock identity, timezone-aware epoch, uncertainty in seconds or explicit null. |
| `frame` | Declared thermal state frame; no implicit spatial interpretation. |
| `request` | Existing bounded thermal request template, excluding inputs and observations. Includes model, prior, Q, R, interval and prospective selection settings. |
| `series` | Exact-byte artifact references for shell, core_reference, heat_power and ambient streams. |
| `channels` | Device identity, raw/calibration/application references, quantity, unit, declared range and calibration validity interval for each stream. |
| `artifacts` | Bounded exact bytes with canonical base64 and SHA256; every required reference resolves locally. |
| `model_evidence_refs`, `held_out` | Evidence for model/prior/noise choices and declared exclusion of the reference from fitting, prior, noise tuning, conditioning and criteria selection. |
| `assumptions`, `configuration` | Fixed contract declarations printed in the implementation and example. |
| `acceptance` | Explicit absolute/relative comparison tolerances and retained criteria reference, or null. No automatic physical acceptance threshold. |

The streams use the existing `ciw.observation-stream.v1` and covariance contracts.
Each row binds the model, asset, clock, frame, quantity, units and source lineage.
Shell and core quantities are `shell_temperature` and `core_temperature` in K;
heat power is `heat_power` in W and ambient is `temperature` in K. These coordinate
names prevent a shell measurement being silently compared as core temperature.

All streams have 1..128 equally spaced rows, first timestamp `dt`. Power and
ambient rows declare constant values over the preceding interval. Their missing
values are refused. Missing shell/reference values remain null. No interpolation,
clock alignment, unit conversion or dropout imputation occurs here.

The inner kernel request retains the existing `julia.thermal-design.v1` contract
identifier for compatibility. The enclosing executable operation and runtime are
explicitly `ciw.measured-thermal.v1` and the Python reference; no Julia execution
is implied by that inner identifier.

The request compiler always constructs `[null, shell]` observation rows. The core
reference cannot enter the conditioning calculation. Changing reference values or
comparison thresholds leaves the model, observer and prospective selection
unchanged. Distinct reference device/raw/application identities are checked;
independence and exclusion from past fitting remain evidence-backed declarations,
not facts proved by the adapter. Explicit held-out data references in inference
provenance are refused. Uncertainty ancestry must resolve to retained covariance
artifacts by covariance identity; dangling evidence and cycles are refused.

Calibration validity uses the declared acquisition interval, with `[valid_from,
valid_until)` semantics. Known clock uncertainty expands that interval. Unknown
clock uncertainty stays null and applicability is labelled declared-times-only.
Expiry today does not invalidate a recording acquired inside its declared interval.
Raw bytes, certificates and application receipts are retained and hash checked;
application arithmetic, certificate authenticity and traceability are not checked.

## Inspect uncertainty and held-out comparison

The native result retains the complete 2x2 conditional covariance, innovations,
missingness and estimator trace. Filter Q/R remain declared statistical model
inputs. They are not inferred from calibration certificates or substituted for
recorded measurement uncertainty. Input, model and clock uncertainty are retained
but not propagated by this kernel. Per-row covariance does not establish temporal
independence, cross-stream covariance or empirical coverage.

The evaluation reports pointwise core errors and RMSE on present reference samples.
PASS/FAIL uses the existing absolute/relative numerical comparison predicate.
Missing reference samples or absent criteria produce INDETERMINATE. An entirely
missing shell stream is explicitly labelled `prediction_only`. Numerical PASS,
calibration applicability and successful replay never grant physical validation,
state admission or permission to actuate equipment.

## Export observations

The existing observation command accepts a measured bundle and exports a standard
scalar stream:

```sh
net science observations --workspace results/thermal/workspace.json \
  --workspace-sha256 '<sha256 of exact saved workspace bytes>' \
  --bundle '<exact bundle_id>' --stage posterior --entity '<asset_id>' \
  --output results/core-estimate.json
net science observations --workspace results/thermal/workspace.json \
  --workspace-sha256 '<same workspace SHA256>' \
  --bundle '<same bundle_id>' --stage reference --entity '<same asset_id>' \
  --output results/core-reference.json
net compare results/core-estimate.json results/core-reference.json --atol '<declared K tolerance>' --json
```

`posterior` is the core estimate with its marginal variance and original estimator
execution. `measurement` is the shell recording; `reference` is the held-out core
recording. Acquisition execution identities and observed/simulated semantics remain
unchanged. Entity renaming and predicted-stage export are refused for this profile.
Exports link source, result, bundle and workspace hashes; they do not launch a new
execution. Full cross-coordinate covariance and calibration/clock declarations
remain in the linked workspace. `net inspect` checks standard stream consistency;
re-export from the exact workspace checks the selected native result.

## MCP delivery and restart

The fixed `measured-thermal` MCP profile binds this same workflow for an external
local client. See [the agent protocol](NET_AGENT_PROTOCOL.md#measured-thermal-profile)
for profile creation, retained result inspection and explicit `--resume`.
The MCP source limit is 48 KiB, separate from the native 1 MiB ingress limit;
excess input refuses without truncation. The adapter adds no solver or
calibration inference. Its result exposes the
existing scientific bundle and explicitly marks correction handoff as unchecked.
An installed-wheel SDK gate exercises the fixture path and process reconnect;
physical validation and State Ledger admission remain separate.

## Remaining pilot gate

Software fixtures do not close the selected physical pilot. That requires a
suitable identified apparatus and model, actual calibrated shell/power/ambient
recordings, an independently calibrated withheld core reference, predeclared
criteria, held-out episodes and human review. The existing independent-provider,
hardware and admission gates remain separate.

The [public dataset assessment](PUBLIC_THERMAL_DATA.md) retains a real research
candidate and its exact retrieval procedure. It does not supply missing
calibration, measured power or model declarations needed to close this gate.
