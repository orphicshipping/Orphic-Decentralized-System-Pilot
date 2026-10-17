# Personal engineering and supervisory investigations

NCP is a private engineering and supervisory workbench, accessible through MCP and an interactive console. The console calls the same MCP host; it owns no scientific execution state. Existing modelling, design, simulation, optimization, Board and research profiles remain available through their original contracts. This additive operational profile connects retained recordings to configuration history, model comparisons, proposed experiments, alarm review and the installed Link command boundary.

The implementation follows [WO-NCP-OE-MCP-20261017](work-orders/WO-NCP-OE-MCP-20261017.md). See [the acceptance record](../validation/operational/ACCEPTANCE.md) for executed evidence and qualification limits. No public deployment or automatic merge is included.

## Installation and first connected journey

Use Python 3.12 and the delivered wheel, or build the candidate checkout:

```sh
python -m venv .venv
.venv/bin/python -m pip install '.[external-work]' -r tools/mcp-operator-console/requirements.txt
.venv/bin/python -m ciw.net operational admit --manifest examples/operational/admission.json --output-dir runtime/personal
```

The output names an immutable `profile-<generation>.json`. The supplied admission contains **synthetic examples**, not acquired measurements. Select that exact returned profile in the following commands. Set `PROFILE` to its absolute path; shell examples use POSIX syntax, with `.venv/Scripts/python.exe` on Windows.

```sh
.venv/bin/python -m ciw.net operational init --profile "$PROFILE"
.venv/bin/python -m ciw.net operational call --profile "$PROFILE" --resume oe_catalog
.venv/bin/python -m ciw.net operational call --profile "$PROFILE" --resume oe_query --arguments '{"source":"recorded","clock":"synthetic.clock","boot":"boot-1","channel":"temperature","start":0,"end":0.5}'
.venv/bin/python -m ciw.net operational call --profile "$PROFILE" --resume oe_query --arguments '{"source":"recorded","clock":"synthetic.clock","boot":"boot-1","channel":"temperature","start":0.5,"end":2}'
```

Keep the two returned `record` references as `BASELINE_QUERY` and `CHANGED_QUERY`. Pass their exact values to `oe_change` as `baseline` and `changed`. The finding retains both interval queries, exact configurations, field differences, response values and confounding alternatives. Queries are half-open in the named source clock and boot. Missing timestamps are unplaced; receipt times are never substituted for sample times.

```sh
.venv/bin/python -m ciw.net operational call --profile "$PROFILE" --resume net_execute --arguments '{"source":"assessment","graph":"assessment-graph","attempt":"investigation-001"}'
.venv/bin/python -m ciw.net operational call --profile "$PROFILE" --resume oe_model_support --arguments '{"attempt":"investigation-001"}'
```

The first call executes an approved fixed graph through AgentHost, Session, OperationRegistry and the existing graph runner. Assessment and verification have separate operation, execution, result and verification identities. Verification checks numerical report consistency; its scope is not independent physical validation. The second call assembles a finding from the retained result without dispatching another provider.

Pass that finding's `record` and `"template":"resolve"` to `oe_propose`. Inspect the proposal with `oe_inspect`. It includes alternatives, distinguishing observations, procedure, controlled/observed variables, prerequisites, limits, fixed decision rule, unknown predictive capability, dependencies and **approval not granted**. `oe_search` searches the structured record within this investigation. `oe_annotation` appends review context.

Reopen with `--resume`. Retry the identical `net_execute` attempt to recover its original response. Deliberate fresh computation uses `net_replay` with `original_attempt` and a distinct `new_attempt`; it preserves input evidence and allocates new execution/result occurrences. Interrupted claimed attempts remain unknown. Reopening never reconnects a device or resumes a job automatically.

## Personal MCP and interactive console

Admission writes a resolved `mcp-<generation>.json` beside the profile. After the explicit `init` above, add that file's `mcpServers` entry to your MCP client. Its command uses the selected installed Python interpreter, `ciw.net operational serve`, the exact absolute profile, `--sdk` and `--resume`. Stdio works independently of any remote deployment.

MCP tools expose the same domain methods as the CLI. Resources are:

- `ncp://investigations/{digest}`: the authorized frozen investigation catalog.
- `ncp://operational-records/{digest}`: an owned query, finding, proposal, alarm or review record.

Digests are the 64 hexadecimal characters after a returned `sha256:` prefix. Source paths, URLs, imports, device targets, policy edits, provider selectors and grant changes are not tool arguments. Discovery does not grant execution.

Launch the existing console against this profile:

```sh
.venv/bin/python tools/mcp-operator-console/create_token.py --output runtime/console.token
.venv/bin/python tools/mcp-operator-console/console.py --python /absolute/path/.venv/bin/python --profile "$PROFILE" --surface operational --resume --state-dir runtime/console --token-file runtime/console.token
```

Open `http://127.0.0.1:8765` locally and unlock with the token. The discovered catalog contains the operational and NET tools. Calls that retain or execute work require console confirmation; approval of a supervisory command is a separate server-side operator act. Console results retain the same identities returned to other MCP clients. One host owns its execution output; close that stdio host before opening a second one against the same profile.

Export the same retained evidence into an interactive offline view:

```sh
.venv/bin/python -m ciw.net operational view --profile "$PROFILE" --resume --output runtime/investigation.html
.venv/bin/python -m ciw.net workbench --operational-profile "$PROFILE" --resume --output runtime/workbench-investigation.html
.venv/bin/python -m ciw.net operational export --profile "$PROFILE" --resume --output runtime/investigation.json
```

The view selects retained sources, interval/channel subsets, findings and proposals. Its data and packaged assets are local; network connections are disabled by CSP. It displays exported query pages and their coverage limits. Refresh the export to see later retained records. `attach-workbench` adds the same projection as an existing typed-data artifact to a supplied retained Session workspace, preserving its original execution/result identities.

## Acquisition continuity and immutable admission

A collector runs as a separate operator process. Chat and console disconnection do not own its lifetime. Operator-prepared `ncp.operational-collector-profile.v1` files bind one of `retained-files`, `link-receive` or `nvml`; no saved data imports executable adapters. Example:

```sh
.venv/bin/python -m ciw.net operational collect --manifest examples/operational/collector.json
.venv/bin/python -m ciw.net operational collector-state --journal examples/operational/recordings.sqlite --id file-recorder
.venv/bin/python -m ciw.net operational collector-stop --journal examples/operational/recordings.sqlite --id file-recorder
```

The example only re-reads explicit fixture files. `link-receive` delegates finite byte ingress and capture to the existing Link gateway/Journal. Physical GaugeLink serial acquisition remains its reviewed receive-only application; admit its closed native Link capture. `nvml` opens the existing read-only `NVMLEnergyCounter` with operator-selected device index, acquisition-host ID, boot ID and at most 64 readings per segment. It never launches a diagnostic workload. Failed readings and host-call brackets remain raw.

Each collector occurrence bounds segment count (1–64), retained source bytes (up to 8 MiB), segment size (512 KiB), cadence (0–60 s), and stop-without-overwrite quota behavior. The existing Journal also enforces global bounded storage. Reserved control headroom lets backpressure/overflow status remain inspectable. A stopped or failed occurrence requires explicit `--restart`; an abandoned RUNNING occurrence first requires `collector-recover`. Recovery records interrupted/unknown status without opening the source. A restart allocates a fresh occurrence and retains the previous receipt.

`operational_cli.admit_segment` accepts an exact closed segment receipt plus explicit interpretation/configuration declarations. It binds the new source to that receipt and creates a new immutable investigation generation. Ordinary `admit` freezes operator-selected recordings. Neither path refreshes old AgentHost inputs or expands old grants. Source/configuration/model/policy/template changes and superseding findings invalidate relevant proposals.

## Supervisory review and command authorization

Operator-prepared `supervision.alarms` rules bind source, channel, clock, boot, high threshold and maximum sample age. `oe_alarm_evaluate` uses a retained query and an explicit `as_of` in the same source clock. Invalid/missing samples, unknown timing and stale data produce UNKNOWN; connectivity, validity and freshness stay separate. Alarm acknowledgments require the separate `acknowledge` grant and append review records. They never reset equipment alarms, clear conditions or alter local interlocks.

`supervision.configuration_candidates` contains fixed Link candidates and required proposal-template identities. `oe_command_prepare` links an inspectable proposal, creates the existing native Link configuration plan and a separately identified structural-validation record. It does not approve or apply the change.

After reviewing the concrete command, an operator can run `approve-command` with `--command`, an identical `--expected-ref`, their `--approval-ref`, `--principal` and a bounded `--expires-ns` before the command plan expires. Approval is not exposed as an MCP tool. `oe_command_dispatch` then requires both that exact unexpired approval and a separate `supervise` grant. Its attempt is claimed durably before dispatch; interrupted outcomes are never automatically repeated.

**The current Link runtime applies synthetic configuration changes only and refuses physical configuration, calibration, update and CONTROL dispatch.** This integration preserves that implementation limit. A future physical adapter must implement its own explicit capability, authorization, installed-chain validation and independent protection contracts. This work neither grants a physical command nor represents a simulation as one. No stress tests, driver changes, GPU power changes or actuator commands are included. The 80°C station prerequisite remains strict (`temperature < 80`; equality and invalid readings block); the new operational profile provides no workload-launch path.

## Model and source applicability

| Source family | Inspection | Numerical adapter | Important limit |
|---|---|---|---|
| Native scalar recording / observation stream | Exact source, channels, units, frames, clocks and declared configurations | Fixed paired-reference comparison of retained predictions | No new prediction is claimed for the retained-prediction adapter |
| Measured thermal source | Four original calibrated streams and native context | Existing two-capacity thermal workflow, prediction/estimation and guarded joint-reference comparison | Conditional marginals do not establish a complete joint uncertainty budget |
| Link / GaugeLink native capture | Original wire commitments, manifest/configuration/boot and receipt context | Existing scalar decoding, affine calibration, shared-covariance mean model and arithmetic audit connected through the same AgentHost | Report-receipt ticks are not sensor sample times; no implicit clock conversion |
| Measurement bundle | Existing frozen MeasurementHost records and lineage | Earlier native results stay authoritative | No reinterpretation of unknown acquisition time |
| Calibrated window | Original pre-correction samples, profile and full joint covariance | Native calibrated-window workflow connected through operator-bound exact role stacks | Missing stacks refuse; native clock/calibration/covariance checks remain authoritative |
| Residual-monitor declaration and retained upstream windows | Exact original window identities and lineage | Existing pinned FDIR/OIT monitor connected through operator-bound stacks | Temporal dependence stays unknown; anomaly detection cannot uniquely nominate a physical fault |
| GPU telemetry | Exact NVML identity, errors, nulls and call brackets | Retained inspection | Context reads have unknown instrument sample timestamps; no GPU workload |
| Elapsed-Gauge-Meter research-study/1 | Candidate/observation separation, durations/work/energy and asserted provenance | Upstream parser/generator/model remains separately owned | Structural projection only; native validation, ratios, ranking, comparison and physical timing qualification are not inferred |

GaugeLink development reference: `b09b6d267063361fbcc34016ba126ec6a660c1fd`; overview main: `10ccca27ab4cb0612a04e92ca6327398344042d5`. EGM reference: `169bdcd8057b80abda4670a1d9133be54c7f8fc5`. The integration does not install either repository or claim physical compatibility. EGM retains its existing `microgauge-*` schema identities and its prohibition on cross-record optimization conclusions.

Thermal model support uses the native held-out reference and fixed assumptions. Joint comparison uncertainty must be complete, total-scope and explicitly declared for definitive agreement/disagreement. Unknown covariance, missing calibration/alignment applicability and unsupported combinations remain held. Numerical agreement is conditional evidence about the declared model and inputs; it does not identify a unique fault, authenticate calibration or establish physical truth. Material/lot/formulation, geometry and process revisions remain in the source's explicit context where supplied.

## Authenticated laptop/worker transport

`serve-http --config <operator-config> --port 18767` uses the existing Cloudflare Access RS256 verifier and SDK Streamable HTTP seam. The origin binds loopback only, verifies exact Host/Origin and signed audience/subject on every request, and binds a fixed caller, worker and investigation. Revocation denies subsequent calls without undoing dispatched work. Remote tool arguments cannot select another worker or filesystem/URL target.

Configuration schema `ncp.operational-access.v1` requires `profile`, `resume`, `issuer`, `endpoint`, `caller`, `worker`, `investigation`. The endpoint contains `public_url` ending `/operational/mcp`, the Access `audience`, `role: worker`, and the exact subject-to-caller mapping. Issuer must be the selected `https://<organization>.cloudflareaccess.com` origin. These values come from your private deployment; none are fabricated by preparation. The local workflow is independently usable. Signed-token and local transport tests do not qualify an actual remote network, credentials, OAuth edge or physical worker installation.

Revoke an investigation using `net operational revoke --profile "$PROFILE"`. Inspection/reconnect, numerical dispatch, supervisory dispatch and authenticated requests all check the revocation state. Export before revocation if a retained review copy is needed. Revocation does not erase evidence.


## Corrected collection and installed launch

The generated MCP configuration uses the selected installed interpreter with `-I`.
Use that generated file unchanged. An earlier launch file without `-I` is obsolete;
readmission preserves it and emits a corrected launch with a content suffix.
Initialization remains explicit before `--resume`. Working-directory packages and
ambient Python import paths cannot substitute for the installed package.

Link receive uses one native incremental decoder per transport session. Native
frames commit before later frames are parsed. Arbitrary byte fragmentation is
supported; incomplete/corrupt tails retain their committed prefix and report a
failure. Each closed capture contains only that segment's frames. Operator collector
bindings may set `segment_records` from 1 to 256 (default 1), subject to the native
and retained-byte limits. Choose the boundary required by the scientific source
contract; a wire frame alone does not guarantee a complete scalar CRLF recording.
Stop is monotone within an occurrence and survives concurrent acquisition saves.
An in-flight read may finish and be retained; stop never authorizes another read.

`oe_rows` provides bounded discovery of original observations, with optional
`unplaced_only`, `offset` and `limit`. Unknown-time rows remain outside interval
attribution. Their native acquisition origin and the operator's recording/replay
origin are separate fields. The existing console exposes the same read operation;
the offline view includes an unknown-sample-time selector and exact row identities.

## Native engineering bindings

The model `engine` may be `thermal`, `reference`, `scalar`, `calibrated-window` or
`residual-monitor`. A scalar request is the unchanged native scalar-study request;
its capture must equal the selected source. Calibrated-window requests must be the
exact admitted source bytes. A residual-monitor request contains `source` (the
native packed declaration bytes) and `upstreams` (the exact native window catalog).
The admission family for its declaration is `residual-monitor`. Native validators
reject substituted windows and replayed samples before execution.

The latter three adapters use a fixed policy object containing `unit`,
`coverage_factor`, `max_abs_error` and `max_standard_uncertainty`. Units must match
native declarations. Their native estimates, residuals, covariance, operations,
execution/result identities and verification remain available in the retained
model finding and AgentHost artifacts. Without an independent paired comparison
and total budget, model support remains INSUFFICIENT_EVIDENCE. A successful native
workflow or conditional arithmetic check cannot supply that missing evidence.

Admission manifests may additionally supply a `providers` object. Its keys are
`calibrated-window` or `residual-monitor`; each value maps exactly the native
role names to operator-selected repository paths, resolved relative to the
admission manifest. Window roles are tbrt/mcur/stfe/gsie/set; monitor roles are
fdir/oit. The installed native validators enforce their existing exact revision,
source-tree, interpreter and dependency contracts at dispatch. Paths are never MCP
arguments or retained-source-selected imports. Catalog status distinguishes an
available built-in adapter, an operator-bound stack awaiting its pin check, and an
unavailable provider. Retained inspection requires no native dispatch.

## Findings and proposed tests

Each changed configuration field gets its own hypothesis with the actual before/
after values, selected response, named confounders and retained evidence. Model
findings retain the actual comparison outcome/reason and fixed policy. Proposed
tests bind these hypothesis identities to controlled/observed variables,
distinguishing evidence, decision targets, prerequisites and resource limits.
Unsupported intervention predictions remain explicitly absent and information gain
is not quantified. A prepared plan is held for new evidence and applicability
review; it neither executes nor grants approval. Findings for different source/model
scopes coexist. A later finding for the same scope invalidates its earlier proposal.
Supervisory configuration preparation additionally requires the proposal's evidence
to bind the selected command source.

## Reproduce complete acceptance

Run the focused operational suites, build/install a wheel, then invoke these with
that installed interpreter and `-I`:

- `scripts/check_operational_installed.py --output-dir <new-directory>` executes
  generated-config stdio J1–J5, scalar native calculations, reconnect, replay,
  retained resources and working-directory shadow resistance.
- `scripts/check_operational_http.py --output-dir <new-directory>` executes real
  SDK HTTP across a separate loopback worker process, RSA fixture signatures,
  reconnect/restart, abrupt death with unknown-outcome recovery and revocation.
  Its fixture public key and kill marker belong only to this test driver; production
  service configuration continues to use the existing Access verifier and JWKS.
- `scripts/check_operational_browser.cjs` exercises the actual existing console,
  SDK, installed package and offline view, including unknown-time observations.

With `CIW_ACQUIRED_STREAM_STACK_ROOT` pointing at exact role-named native checkouts,
`tests/test_operational_native.py` executes native window and monitor journeys.
Use isolated pytest outside the checkout (`-I`, `-c /dev/null`, importlib mode) to
verify the installed artifact. The existing acquired-stream gate now includes those
cases and retains its original provider-access and no-skip requirements.

Loopback HTTP acceptance is software evidence. A production Access/TLS/network
installation and physical acquisition/control qualification still require their
selected environments, credentials, devices and independent measurement evidence.
