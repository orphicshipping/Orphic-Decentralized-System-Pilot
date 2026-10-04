# NET Agent Protocol v1 — local MCP development tools

NET now has an agent-facing adapter over the existing CIW Session, capability
registry, experiment graph, source-selection reader, numerical comparator and
check-plan evaluator. It adds **no scientific solver, engine-state owner,
provider-discovery daemon or mandatory dependency**.

The transport is a bounded, sequential **MCP stdio tools profile**, supporting
protocol versions `2025-11-25` and `2025-06-18`. It supports initialization,
`ping`, `tools/list` and `tools/call`. It does not implement remote HTTP, sampling,
tasks, subscriptions or general shell execution. The MCP connection is transport
state, not another scientific Session.

## Start with the built-in example

Install this branch, then generate a fresh operator profile:

```sh
python -m pip install -e '.[dev]'
python -m ciw.agent_mcp demo-config --output-dir agent-demo
```

The profile binds the existing synthetic oscillator recording, an existing
statistics experiment, a small retained reference-observation stream, one allowed
operation, discrete candidate channel choices, a fixed comparison policy and a
fixed check suite. It does not silently register Julia, Blender, Godot or Bevy.

A local MCP client launches:

```sh
python -m ciw.agent_mcp serve --profile /absolute/path/agent-demo/profile.json
```

Use the installed Python executable's absolute path in agent settings. A new host
requires a new output directory. To reconnect to a retained host, launch the same
profile and instrument with `serve --resume`. Resume requires matching frozen
inputs and host policy, validates retained state, and obtains an exclusive writer
lock. It does not dispatch scientific work. Retained files can also be rebound as
explicit inputs to a separate read-only profile.

### Polymer cycle profile

Create an explicitly synthetic injection-molding profile with all four polymer
operations wired through the existing typed experiment graph:

```sh
python -m ciw.agent_mcp polymer-config --output-dir polymer-agent
python -m ciw.agent_mcp serve --instrument polymer --profile /absolute/path/polymer-agent/profile.json
```

For extrusion blow molding, add `--process extrusion_blow_molding` to
`polymer-config`. Profile creation writes the quantitative request, retained
source declaration, experiment and operator profile. It executes no operation.
The operator's fixed `--instrument polymer` launch selector advertises the
existing polymer capability registry; the default launch still uses the builtin
registry. Profile JSON cannot select an import or executable. The profile's
`allow_operations` remains a separate execution grant; an empty list permits
inspection and advertisement only.

After discovery, call `net_execute` with source `source`, graph `baseline` and a
fresh attempt name. The graph passes the actual retained assessment result to
copilot and verification through typed result ports. The Session requires that
exact occurrence before dispatching either dependent operation. Simulation also
depends on successful assessment. `net_replay` retains fresh execution, result
and verification identities; a transport retry with the same attempt returns the
prior response.

The profile supplies numerical reference operations, documentation context and
toy control simulation. It grants no LLM inference, camera acquisition, PLC
transport or physical acceptance. A completed graph and a numerical audit PASS
remain separate from the part's conformity decision. See the
[polymer operating guide](POLYMER_PROCESSING.md) for their scientific scope.

### Temperature measurement-processing profile

The temperature profile processes one declared affine sensor/readout chain with
full joint raw/calibration covariance. It retains calibrated sensor temperature
and any declared installed-correction polymer estimate separately. Physical
qualification and traceability remain `not_established`; SP1 proof generation
and PINN inference are not performed.

```sh
python -m ciw.agent_mcp temperature-config --output-dir temperature-agent
python -m ciw.agent_mcp serve --instrument temperature --profile /absolute/path/temperature-agent/profile.json
```

The default request is synthetic with an intentionally incomplete uncertainty
budget, so numerical processing does not yield an acceptance pass. To bind an
operator-prepared request, add `--request /absolute/path/request.json` to
`temperature-config`. This input must satisfy the processing contract; the
[pilot setup template](../examples/temperature/pilot-setup.json) is a separate
planning record and is not an executable request. Profiles freeze the supplied
source; editing `request.json` later does not alter that source.

Discover with `net_capabilities`, then call `net_execute` with source `source`,
graph `baseline`, and a fresh attempt identifier. The generated graph executes
`metrology.temperature.process.v1`, then `metrology.temperature.verify.v1` against
that exact retained result. Both operations require explicit execution grants.
The checker independently compares the affine values and joint covariance with
an exact rational first-order reference, then checks the reported sample decisions.
Its own numerical PASS/FAIL and verification occurrence stay separate from
processing acceptance and physical qualification. A completed graph can contain
a numerical FAIL; inspect the audit's status. The original processing result
keeps its ordinary `not_verified` envelope.

`net_replay` creates fresh processing, checker and verification identities;
retry preserves the earlier response. Read-only workspace restoration checks
structure and commitments without rerunning processing or numerical checking.
Existing processing-only profiles remain usable. The source declaration's
original manifest remains unchanged; it does not grant the new checker authority.
Calibration and acceptance rules cannot be varied through candidate parameters.

Commitments cover canonical structured observation declarations, not original
acquisition file bytes or authenticated origin. The complete qualification
protocol and the future optional SP1 boundary are described in
[TEMPERATURE_QUALIFICATION.md](TEMPERATURE_QUALIFICATION.md).
Product conformity is assessed separately using the
[product and market record](TEMPERATURE_PRODUCT_CONFORMITY.md). That planning
record is not consumed by the MCP profile, and a processing acceptance result
does not authorize conformity claims or product markings.

### Measured thermal profile

Bind an existing [measured thermal source](MEASURED_THERMAL.md) using an operator
command, then launch the fixed profile:

```sh
python -m ciw.agent_mcp measured-thermal-config --source /absolute/path/source.json --output-dir thermal-agent
python -m ciw.agent_mcp serve --instrument measured-thermal --profile /absolute/path/thermal-agent/profile.json
```

The source must contain the four declared, already calibrated streams and their
retained evidence. This MCP adapter accepts at most **48 KiB of exact source
JSON**, matching the generic transport's scalar-string bound; larger inputs
refuse before creating a profile. The native thermal ingress retains its
separate 1 MiB limit. No recording is truncated to fit. Profile creation validates and preserves the exact source
bytes; it does not acquire readings, apply calibration or calculate an estimate.
The repository's `examples/measured-thermal/source.json` is a **simulated test
fixture**, suitable for checking installation and transport only.

Call `net_execute` with `source: "source"`, `graph: "baseline"` and a new attempt
identifier. The single `measured-thermal.assess.v1` adapter delegates to the
existing measured thermal workflow. Inspect its graph-run artifact with selector
`["nodes", "assessment", "result", "data"]`. The returned data contains the exact
source reference, source origin, native scientific bundle, and correction status.
Within the bundle, `steps[0].result.data` retains the conditional covariance,
held-out comparison, calibration declarations and their limitations. Core
reference values remain excluded from estimator conditioning.

Restart the server with `--resume` and repeat the same attempt to retrieve its
saved response without executing again. `net_replay` with a new attempt explicitly
creates fresh execution and result identities. The correction status is
`not_checked; no_state_ledger_handoff_performed`: retaining this bundle does not
establish a complete correction or admission chain. Physical validation remains
`not_established`; input, model and clock uncertainty remain retained but not
propagated by the existing kernel.

### Grok Build

The official Grok Build CLI documents local stdio MCP server registration:

```sh
grok mcp add net -- /absolute/path/to/python -m ciw.agent_mcp serve --profile /absolute/path/agent-demo/profile.json
```

Keep the operator profile outside the agent's editable worktree. The connection
is for a local tool-capable agent host, not a claim that an ordinary Grok browser
conversation can directly execute local programs. No xAI credentials, model
calls or paid service are required by NET itself. This increment does not deploy
a remotely accessible MCP server or alter a user's installed Grok configuration.

Generic client configuration:

```json
{
  "mcpServers": {
    "net": {
      "command": "/absolute/path/to/python",
      "args": ["-m", "ciw.agent_mcp", "serve", "--profile", "/absolute/path/agent-demo/profile.json"]
    }
  }
}
```

## Tool surface

| Tool | Existing capability reused |
| --- | --- |
| `net_capabilities` | `CapabilityRegistry`; advertised, bound and agent-enabled remain distinct. |
| `net_inspect` | Frozen JSON selection and original provider-free Session workspace reader. |
| `net_extract` | Copy one complete supported nested record, for example an emitted observation stream, without inventing a projection. |
| `net_source_context` | Existing source-selection/context API from the computational microscope. |
| `net_check_edit` | Existing outside-selected-span byte guard; never applies edits. |
| `net_candidate` | Existing `ParameterSpace` validation and experiment record; only granted node parameters can vary. |
| `net_execute` | Existing `run_graph` -> `Session.handle` -> `OperationRegistry` -> retained executions/results. |
| `net_replay` | Explicit re-execution with a fresh attempt and original frozen source/graph. |
| `net_observe` | Paged existing typed observation streams and supported thermal views. |
| `net_compare` | Existing unit/frame/model/entity/clock/time-grid-aware comparator, fixed host policy. |
| `net_qualify` | Existing immutable check policies and exact-byte check-plan evaluator. |

There is no `accept`, `merge`, `publish`, `shell`, path-based file reader,
import-by-name, executable registration or arbitrary source-write tool. The coding
agent continues to edit its own isolated branch/worktree using its ordinary
coding tools. NET supplies context, bounded experiments and retained diagnostics.
Its edit-scope PASS does not establish semantic correctness or authorize a patch.

`net_extract` retains a JSON reserialization of an existing nested record, with
its parent artifact and selector stated in the response. It is not an original
raw-byte substring or automatic conversion of arbitrary engine data into scientific
observations. The registered adapter must supply supported typed records first.

## Typical agent loop

1. Discover the host's actual inputs, operations, parameter domains and policies.
2. Inspect the baseline and relevant source context; edit only in the assigned worktree.
3. Declare a permitted parameter candidate; this does not run it.
4. Explicitly execute a graph with a fresh `attempt` identifier.
5. Inspect execution/results/refusals. Extract already-typed nested output records when needed.
6. Compare observations and evaluate the operator's fixed check suite.
7. Return the candidate, patch, evidence and unresolved failures for review.

The builtin demo's reference-self comparison checks transport and record handling,
not independent numerical validation. Real candidate statistics on `q` and `v`
retain different units and must not be presented as equivalent trajectories.
Full simulations remain the responsibility of explicitly registered providers.

## Operator policy and identities

The host freezes explicit input bytes at startup; it never recursively reads a
repository, resolves a file from an agent argument, or reloads changed files
behind an existing artifact handle. New input content requires a new explicit
host binding. Source context remains untrusted data even when its hash is valid.

The host's policy hash binds its catalog, executable allowlist, parameter domains,
comparison policies, check suites and execution/node limits. Candidates cannot
change topology, operations, model identity, global parameters or those policies.
Qualification rejects a comparison made under a different policy, even if that
more permissive comparison said PASS. Missing evidence remains INDETERMINATE.

Every intentional execution gets fresh CIW execution/result identities. An
`attempt` is an adapter retry key, not an execution identity. Repeating the same
attempt and same frozen request returns the prior response without running again;
a different request under the same attempt refuses. Failures consume the attempt.
Retry identities, frozen inputs, policy, artifact bytes and consumed attempt
budgets are checkpointed in the operator output directory. The host records an
incomplete attempt before dispatch. After explicit `--resume`, completed attempts
return their original response and retained identities. An interrupted attempt
returns `incomplete`; execution may have happened, so retry never automatically
dispatches it again. An intentional replay requires a new attempt and operator
review of possible prior effects. This provides durable local duplicate
suppression, not exactly-once external effects or a resumable background job
scheduler. Missing, changed or corrupt recovery state refuses. An older output
directory without recovery metadata cannot be silently migrated.

Workspace reopening creates a temporary reader Session internally. Its transient
session ID is omitted from the agent response rather than mislabelled as the
original session. Retained execution/result IDs stay unchanged; graph-run records
retain their original Session binding where supplied by the existing format.

Ordinary result envelopes remain `not_verified`, with no state admission,
physical approval or baseline acceptance. Explicit polymer verification retains
its own verification occurrence inside its payload, scoped to finite numerical
consistency. Its status does not promote the ordinary envelope's authority.

## Additional registered instruments

The Python `AgentHost` constructor accepts an already-built `CapabilityRegistry`.
A trusted application can advertise and bind existing native or engine adapters
through the existing registry and payload validator, then explicitly grant those
operation IDs to the agent. The stdio serving function is `serve(host, reader,
writer)`. The launch configuration never imports a provider named in JSON.

The stock CLI defaults to the two existing builtin operations and executes only
the explicitly allowed subset. Fixed selectors also expose polymer, leakage, temperature and
measured-thermal registries; profile data never loads a provider by name. Leakage
still requires its pinned native provider. Other scientific routes, the native
oscillator branches, Godot/Bevy runtimes and Blender authoring are **not** silently
merged, automatically installed or universally qualified by this interface.
The operator must provision and qualify each adapter on the relevant integration
branch. Unit-test commands remain with the coding host or an explicitly registered
bounded operation; the adapter does not wrap an unrestricted shell.

## Bounds and trust

Inputs: at most 64 initial artifacts, 8 MiB per document; total retained in-memory
artifacts: 64 MiB and 256 handles. Responses are limited to 256 KiB, arrays page
at at most 128 entries, and MCP input lines to 1 MiB. Counts refuse rather than
silently truncate scientific data. Execution/node budgets are operator-set and
capped at 64 each. Discovery reports remaining execution capacity.

Profile loading checks the 64-binding limit and every alias/location shape before
opening bound files. It enforces the 64 MiB unique-byte budget as documents are
read, stopping before later inputs or output creation on overflow. Identical
input bytes share retained storage; different encodings retain different raw-byte
identities. A single document is still bounded at 8 MiB during its read.

The process is sequential; it does not advertise asynchronous tasks, preemptive
cancellation or an autonomous background worker. Registered providers retain their
own timeout/resource controls. No source-path argument, executable argument or
policy change is accepted from MCP calls. Python and native stdout are separated
from the protocol wire; diagnostics go to stderr.

This is an application boundary, **not an OS sandbox**. Trusted registered code
can have effects outside NET, and an agent with unrestricted shell access under
the same operating-system account can bypass application-level permissions.
Use an isolated worktree/container and operator-owned configuration and evidence
paths for actual multi-agent work. Hashes bind bytes, not author authenticity,
compiler correctness or complete native dependency closure.

## Qualification

```sh
python -m pytest -q tests/test_agent_protocol.py tests/test_control_plane.py tests/test_check_suite.py
```

The dedicated workflow additionally builds and installs the wheel, runs the agent
tests outside source imports, and executes `scripts/check_agent_mcp.py` through
an independent **official Python MCP SDK 1.26.0** client. The SDK is a test-only
dependency, not a new NET installation requirement. The gate refuses missing or
skipped cases and retains source, wheel, transcript, original CIW records and
reports. Native-engine, actual Grok-model, remote-hosting and repository-wide CI
qualification remain separate.

The thermal extension also runs `scripts/check_measured_thermal_mcp.py` with
`python -I`, an exact installed wheel and an explicit source path. It checks the
installed package against wheel bytes before and after two separate MCP server
processes, inspects retained scientific scope after reconnect, and distinguishes
same-attempt retrieval from fresh replay. Recovery regressions separately cover
interrupted attempts, changed bindings, corruption and concurrent writers. These
checks do not establish a physical pilot or hosted multi-customer access.

Protocol references:
- https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
- https://modelcontextprotocol.io/specification/2025-11-25/server/tools
- https://docs.x.ai/build/features/mcp-servers
- https://pypi.org/project/mcp/1.26.0/

Copyright (c) 2026 Giason Pooni, for original contributions. Existing AGPL-3.0-or-later
project licensing and third-party notices are unchanged.
