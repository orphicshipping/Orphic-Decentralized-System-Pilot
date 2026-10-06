# Retained device comparison and read-only MCP

This is a bounded Physical Runtime increment on Device Link PR #204, exact parent
`03bb19e3c6dac6c30ae0f1ee684a8f9a96611a70`. It consumes that existing Link
investigation, uses `control_checks.compare`, executes through the existing
CapabilityRegistry/OperationRegistry and Session, and exposes a read-only host
through the existing `agent_mcp.Server` / stdio transport.

**It is not a second device registry, ingestion service, MCP server, data bridge,
scientific model, calibration engine, or equipment controller.**

## Execute a software example

After installing this candidate, including the repository's unchanged pins:

```sh
python -m ciw.device_review_cli demo --profile GaugeLink --output-dir gauge-demo
python -m ciw.device_review_cli inspect gauge-demo/review
python -m ciw.device_review_cli replay gauge-demo/review --output-dir gauge-replay
python -m ciw.device_review_cli mcp gauge-demo/review
```

The last command uses the existing newline-delimited JSON-RPC stdio server. An
MCP client initializes the session normally. Exactly three tools are available:
`net_device_review`, `net_device_observations`, and `net_device_reference`.
Only the latter two accept arguments: `offset` (0..256) and `limit` (1..16).
No tool accepts a path, executable, endpoint, authority grant, or operation name.
Execution/replay/control requests are refused, not merely marked with hints.

For existing retained data:

```sh
python -m ciw.device_review_cli run LINK_INVESTIGATION \
  --reference reference.json --request request.json --output-dir review-new
python -m ciw.device_review_cli capabilities
```

`reference.json` must be the existing sealed `ciw.observation-stream.v1`, with
explicit `reference` provenance semantics. Its model, entity, quantity, units,
frame, clock and time grid must actually be comparable. The input is not silently
converted, resampled, interpolated or relabelled. The exact reference bytes,
including formatting, are retained. Each referenced original source remains a
provenance declaration, not authenticated evidence unless separately established.

`request.json` has exactly four fields:

```json
{"channel_id":"CH0","atol":0.05,"rtol":0.0,"purpose":"explicit numerical comparison only"}
```

The tolerance is in the selected channel's units. The existing numerical rule is
`abs(left-right) <= atol + rtol*abs(right)`, componentwise on the exact grid.
These are caller-declared tolerances, not a statistical confidence interval or
standard/compliance threshold. Missing/incompatible data retain an INDETERMINATE
comparison. A numerical FAIL is retained, not converted into a missing result.
Malformed declarations refuse; an accepted invocation that fails retains the
existing Session execution refusal and no successful result.

CLI exit 0 means the command's declared operation succeeded and a comparison, if
present, numerically passed. Exit 1 means a retained FAIL/INDETERMINATE comparison;
exit 2 means refusal. None authorizes material release or physical intervention.

## Original data, complete coverage, distinct identities

The review directory retains exact original `link/capture.json` and
`link/workspace.json`, exact `reference.json` and `request.json`, and a new
ordinary `workspace.json`. The review source embeds the original Link run,
execution and result, without modifying them. Original Link evidence/occurrence
IDs, device/manifest/boot identities, raw wire bytes, manufacturing context,
calibration declarations, full joint covariance and native mapping receipts
remain inspectable through those unchanged records.

A channel is selected using retained measurement and native-observation mappings,
not by array position or quantity name alone. Other channels remain in the source.
Gaps and withheld observations remain visible. Numerical PASS applies only to
selected projected samples; it does not assert capture completeness or qualify
any withheld samples. A missing device clock cannot become host acquisition time.

The comparison does not use covariance to claim statistical confidence. Full
covariance is retained unchanged with its ancestry; it is not diagonalized or
converted into an assumed uncertainty budget. Calibration application and
uncertainty-aware metrological decisions still require their existing qualified
operations and empirical evidence.

The synthetic examples use explicit author-selected nominal references on the
same native grid: GaugeLink 25 mm / 0.05 mm and ThermalLink 296.15 K / 0.2 K.
They are software tests, not physical tolerances, safety limits or calibrations.
`reference-declaration.json` retains the source of each authored target.

## Offline review and replay

`inspect` and MCP startup validate retained source/result bindings and perform
existing offline numerical-consistency checks. They never call the comparison
executor, a model provider, a device, or a network service. Scratch workspaces
are used for the existing Session reopen validator; original directories remain
unchanged. This is not a claim of zero arithmetic during validation.

Explicit `replay` executes the existing comparison again, preserving exact input
bytes and source evidence while allocating fresh Session execution/result IDs.
It checks for changed input bindings before creating the destination. Original
result occurrences are not overwritten. Outputs must be new, non-symlink paths;
these checks are not a filesystem sandbox against a hostile concurrent operator.

The MCP host loads one fixed validated snapshot at startup. Responses are detached
and paginated. Later file changes do not silently change that snapshot; restarting
requires a new validation. Snapshot IDs are content references, not authentication.
The host has no mutation methods and publishes no agent execution capability.
MCP tool annotations are descriptive hints, not the enforcement boundary:
https://modelcontextprotocol.io/specification/2025-11-25/schema#toolannotations

## Physical Runtime and AgroStore handoff

The supplied seven-plane architecture remains a view of the existing substrate.
Device registration/framing/outbox/sync are inherited from Link; Session and
Workbench remain the shared engineering environment. This increment delivers a
retained numerical comparison and read-only agent access, not the entire Physical
Runtime roadmap. It reuses native observation contracts rather than introducing
a competing `notations.measurement.v1` store or schema.

`demo --profile ThermalLink` exercises the same path for temperature while
retaining the humidity channel. This is an AgroStore-compatible software example,
not a grain-bin digital twin, spoilage model, fan controller or food-safety check.
AgroStore/AgroGrow/AgroField change domain evidence and models, not the runtime's
identity or authority rules. No crop/biological thresholds are invented here.

Parent PR #185 / consolidated #200 own typed dataset, warehouse and Workbench
bridges. No shared lifecycle or bridge file is edited here. The demo uses the
existing native observation/covariance export, whose scientific IDs are unchanged;
this is not a new Parquet/Iceberg exporter or registered dataset family. The
existing Link Dashplate remains intact. The new read-only review snapshot is the
interface for later view integration; no competing live dashboard is added.

OpenTelemetry/OpenObserve stays on its separately defined operational boundary.
No SDK/exporter is installed, and no trace ID enters scientific evidence/proof
inputs. PR #172, #174, the firmware/device-lifecycle owner and private GaugeLink
product files are unchanged. No merge, deployment, radio/serial probing, flashing,
physical actuation, model training, canonical-state admission or release occurs.

## Integrity and failed-computation hardening

This increment keeps the same operation and comparison algorithm. Native outcome
sample/component counts must remain nonnegative JSON integers; generated numeric
metrics must remain finite floats. A resealed result cannot substitute `false`
for `0.0` or `3.0` for the count `3` using Python's numeric equality. Existing
comparison values, decisions and tolerances are unchanged. This extra shape
check is scoped to retained device reviews, not a claim that all unrelated
comparison consumers have been hardened.

A refused comparison does not remove source visibility. Both read-only source
tools return the actual selected native observations or retained reference even
when the comparison has no result. Every page reports
`comparison_execution_status` and `comparison_result_id`, preserving the refusal
and never fabricating a successful computation. A real empty reference remains
empty, distinct from an unavailable comparison. All returned data are detached.
The host loads one validated snapshot, not an independent source re-read on each
MCP call. Evidence paging succeeds as a read even when computation was refused.

### Optional operator-held snapshot anchor

The existing `snapshot_ref` is the SHA-256 content reference of the exact retained
review workspace bytes. Keep that reference through a separately trusted channel
when handing off a review, then require it at inspection, replay or MCP startup:

```sh
python -m ciw.device_review_cli inspect review-new \
  --expected-snapshot-ref sha256:<64-lowercase-hex-digits>
python -m ciw.device_review_cli replay review-new --output-dir replay-new \
  --expected-snapshot-ref sha256:<64-lowercase-hex-digits>
python -m ciw.device_review_cli mcp review-new \
  --expected-snapshot-ref sha256:<64-lowercase-hex-digits>
```

The value is supplied by the operator, never selected by a saved file or an MCP
tool argument. A mismatch refuses before parent restoration, computation or
creation of the replay destination. Even JSON-formatting changes produce a
mismatch; evidence and computation identities are not rewritten to make it match.

`content_anchor_check` is inspection metadata only: `MATCHED` or `NOT_REQUESTED`,
with `authenticates_origin: false`. A matching hash is not a signature, source
authentication, clock/calibration check, computation proof or release permission.
Without a separately trusted expected reference, inspection still checks internal
consistency but does not authenticate the bundle against an external source.
The anchor checks the input of replay; the new output has its own snapshot hash.
No new evidence schema, registry, store or decision authority is introduced.

The source and installed CI stages retain failing JUnit output before propagating
the test failure. The focused gate now requires all 106 review cases without
skips. A configured hosted gate is not itself a passing execution receipt.

## Qualification boundary

Run the focused tests with
`python -m pytest -q tests/test_device_review.py tests/test_device_review_hardening.py`.
The companion CI definition tests source and a noneditable installed wheel on
Linux/Windows and Python 3.11/3.13. A workflow file is not a passing hosted receipt.

Real GaugeLink acquisition, calibration/model execution on physical observations,
measured covariance and clock qualification, grain/food models, complete #200
integration, warehouse ingestion, a live reviewed Dashplate, and supported-platform
release qualification remain separate gates. The retained wrapper is not evidence
that a physical instrument, specimen or predictive model has been qualified.
