# MCP control interface

NCP lets people and agents discover, compose and run engineering workflows with
traceable results. Terminal is its human workbench; MCP is its agent interface.

`ncp control` connects the existing semantic compiler to the existing `AgentHost`
execution owner. It retains plans and durable run identities around that owner.
Scientific execution still uses the same Session, operation registry, provider
bindings and evidence validators used elsewhere in NCP.

## Start a local client

Install the optional pinned transport in the same environment as NCP:

```sh
python -m pip install '.[mcp-control]'
ncp control demo-config --instrument builtin --output-dir control-demo
ncp control serve --profile control-demo/profile.json
```

The last command is a stdio MCP server: configure the client to launch it, rather
than typing JSON into an interactive terminal. A typical client configuration is:

```json
{
  "mcpServers": {
    "ncp-control": {
      "command": "/absolute/path/to/environment/bin/python",
      "args": ["-I", "-m", "ciw.net", "control", "serve", "--profile", "/absolute/path/to/control-demo/profile.json"]
    }
  }
}
```

On Windows select the environment's `Scripts/python.exe` and use absolute paths.
The client owns process startup. Use a new output directory on first launch;
add `--resume` when reopening that exact profile and output. Only one writer may
own an output directory. A graceful stop waits for its worker to finish. After
an abrupt stop, interrupted attempts remain unknown unless the execution owner
has a completed retained response; recovery never automatically repeats work.

`--instrument polymer` and `--instrument temperature` expose their existing
multi-operation graphs; generate matching profiles with `demo-config`.
`--instrument leakage` additionally requires an absolute `--provider-checkout`
for the exact [native FSRT revision](leakage.md#provision-the-existing-native-source).
Optional `--python` selects its explicit native interpreter. These launch options
are operator configuration, never tool arguments or instructions from source data.
The existing `measured-thermal` profile can also be served when its separately
validated source and profile have already been prepared through `ncp agent`.
Generated demo inputs are synthetic, including the native-provider demo.

## One uninterrupted journey

1. Call `ncp_workflows_list` for the wider catalog and provisioning prerequisites.
   Its 29 workflow entries are navigation declarations. Call
   `ncp_capabilities_list` for the contracts actually bound to this host and
   their existing execution grants. `ncp_capability_describe` selects one.
2. Call `ncp_plan_template` with `baseline: "baseline"`. It returns the host's
   semantic graph and permitted parameter domains. Modify only within those
   grants. Nodes may be selected and rewired subject to existing typed-port and
   DAG checks; operation identities and the model remain fixed by the baseline.
3. Call `ncp_plan_create` with `source: "source"`, `baseline: "baseline"` and
   the graph. Inspect the returned compilation and digest. Compilation preserves
   baseline global parameters and parent-checkpoint context and does no execution.
4. Call `ncp_plan_admit` with the returned `plan_id` and `expected_digest`.
   Admission validates the candidate under the operator's existing grants. It
   does not grant new authority or claim engineering acceptance.
5. Call `ncp_run_submit` with the same identifiers and a client-chosen `attempt`.
   Poll `ncp_run_get` using the returned server-generated `run_id`. Retry an
   uncertain submission with the same attempt and plan; this returns the same
   job. A new attempt deliberately requests another execution.
6. Read the returned artifact handles through `ncp_artifact_read`. Use selectors
   and pagination for large records. `ncp_observe`, `ncp_compare` and
   `ncp_qualify` reuse the existing owner's supported observation records and
   fixed policies. They do not invent observations from arbitrary result objects.

Plans, runs and bounded artifact views are also MCP resources:
`ncp://plans/{plan_id}`, `ncp://runs/{run_id}` and
`ncp://artifacts/{artifact_id}`. Artifacts retain the original owner's schema,
hash and lineage. A resource URI is an opaque handle, never a filesystem path.

Every control tool has an output schema and returns `ncp.control-response.v1`
with `status`, `data` and `error`. Plans and jobs have explicit field schemas.
Scientific payloads continue to use their existing native schemas and validators;
the envelope does not replace them. Refusals set MCP `isError` and disclose a
stable error code without echoing exceptions or arbitrary source content.

## Job and authority semantics

| State | Meaning |
| --- | --- |
| queued | Identity and intent retained before dispatch |
| running | Worker started; scientific outcome not yet known |
| completed | Owner finished; engineering PASS/FAIL remains in the result |
| failed | Owner returned a definite non-completed response |
| unknown | No reliable completed outcome; never automatically rerun |
| cancelled | Removed before provider dispatch |

`ncp_run_cancel` cancels queued work. For active work it records cancellation
intent; the provider may still finish. Polling, capability discovery and planning
remain available during computation. Artifact inspection, comparisons and checks
serialize with the original execution owner and can wait for active computation.

One bounded in-process worker executes jobs per host. Plans are limited to 64;
job count and scientific execution/node budgets come from the frozen host profile.
Durability means retained identity and recovery, not a distributed scheduler or
automatic restart of calculations. Retained hashes detect inconsistent local
state; they are not an authenticated history against an administrator who can
rewrite the entire state and policy together.

Plan admission is distinct from baseline acceptance, release, deployment,
calibration and physical equipment control. There are no arbitrary shell, code
installation, machine-write or promotion tools. Missing physical evidence stays
missing. Local stdio uses the operator-selected process and profile; this change
does not expose the compute service as a remote authenticated HTTP endpoint.

## Compatibility and validation

The shared official SDK adapter implements modern MCP `2026-07-28` discovery
and legacy `2025-11-25` initialization. Existing core and standalone semantic
profiles opt into it with `ncp agent serve --sdk` and
`ncp semantic serve --sdk`. Their default legacy adapter remains available.
The migration includes request handling, deterministic catalogs, private cache
metadata and response framing, following the
[official protocol changes](https://modelcontextprotocol.io/specification/2026-07-28/changelog).
Jobs are ordinary typed tools with explicit handles; the optional MCP Tasks
extension and MCP Apps UI are not advertised by this server.

```sh
python -m pip install '.[dev,mcp-control,bench-models,numerical-checks]'
python -m pytest -q tests/test_control_gateway.py tests/test_mcp_sdk.py tests/test_semantic_capabilities.py tests/test_agent_protocol.py tests/test_agent_recovery.py tests/test_operator_catalog.py tests/test_scientific_audit.py
python -m pip wheel . --no-deps --wheel-dir dist
```

Install that wheel in a separate environment with `mcp==2.3.0` and
`jsonschema==4.26.0`, then run `scripts/check_control_mcp.py --output-dir NEW`
using that environment's Python. The script starts real installed MCP processes,
validates output schemas, checks admission refusal and exact digests, executes,
reads retained evidence, restarts on the legacy protocol and confirms an
idempotent retry. It retains a transcript and hashes unchanged source inputs.
Its reference-versus-itself comparison is a transport/policy check, not an
independent numerical accuracy test. Use `--instrument leakage
--provider-checkout ABSOLUTE_PATH` for the provisioned native workflow.

## Subsequent increments

This increment delivers the common plan-to-execution path and durable polling.
Remaining work includes explicit adapters for additional catalog workflows,
more specific scientific result schemas, an independently scoped build and
candidate-qualification service, and optional Terminal/MCP Apps presentation.
A build service must bind an approved source revision and pinned recipe to an
isolated worker, retain the artifact and checks, and keep promotion separately
authorized. Broad catalog discovery does not imply these services are implemented.
