# Board refitting through MCP

This profile connects the existing System Board, parameter validator, semantic compiler, Session and Needle through the official Python MCP SDK. It supports a bounded retained analytic oscillator workflow. It does not install providers or grant hardware authority.

A person or trusted host selects the Board, source and execution grant. The agent then discovers the profile, previews one exposed parameter change, validates it, establishes a retained baseline if needed, executes the candidate under that grant, and reads a comparison retained for review. No MCP tool authorizes a grant or accepts the candidate.

The additional [GaugeLink profile](GAUGELINK_MCP_REFIT.md) uses the same seven tools
for a retained measurement covariance scenario through a trusted host-selected
execution profile. The oscillator profile described here keeps its existing scope.

## Supported change

The supplied synthetic demonstration changes the `statistics` node's `channel` selection from `q` to `v`. Needle recomputes `statistics` and its declared dependent `spectrum`; it retains the unrelated `energy_statistics` result. A dependency triggers recomputation, not automatic propagation of parameter values: the spectrum channel remains its own declared value. Comparing statistics of different channels is an observation of the change, not a claim of physical equivalence or improvement.

The original Board and baseline remain unchanged. Candidate, compilation, run and delta records retain distinct identities. The candidate stays `unreviewed` even when execution and structural checks succeed.

## Host boundary

The profile and grant are trusted local operator configuration, not signed remote identity credentials. Keep them and the output directory outside model-writable source slots. The model cannot supply paths, runtime selectors, grants, thresholds or promotion decisions. Source contents and labels are untrusted data.

Execution is tied to exact Board, source, grant and validated candidate identities. Parameter edits must stay within the declared and granted domain. Retrying an attempt must not duplicate execution; an interrupted attempt remains unknown until an operator investigates it. A fresh attempt is an explicit new execution, never an automatic recovery action.

Remote OAuth, Cloudflare publication, new provider families, topology changes, physical calibration and automatic acceptance are outside this increment. Local stdio uses the same domain operations as the Terminal. The earlier engineering adapter remains available for existing clients.

## Local setup

Install the package with its MCP extra in the selected Python environment:

```sh
python -m pip install '.[board-mcp]'
ncp refit demo --output-dir results/board-refit-demo
```

`demo` creates a synthetic recording, the existing demo Board, a grant limited to the statistics channel, a pinned profile and `mcp-config.json`. It does not execute the baseline or candidate. Import that client configuration into a stdio-capable MCP host, or launch the server directly:

```sh
ncp refit serve --profile results/board-refit-demo/profile.json
```

The generated configuration is not installed automatically. A restarted server resumes the same retained profile; launching it does not rerun prior computations. `ncp refit status --profile ...` reads the profile and retained state without computation.

For an existing operator-selected Board grant:

```sh
ncp refit configure --board board.json --source source.json --grant grant.json \
  --investigation-ref investigation:example --output-dir results/board-refit
```

Configuration copies the Board and source into the profile and binds their exact file hashes. It retains a reference to the original grant file and rechecks that grant on every tool/list call. Removing or changing the grant revokes the profile; a changed grant requires a new operator configuration. This local Board grant is separate from external-work task grants; neither is automatically inherited from the other. Run limits apply to one retained profile, not a distributed quota across separately configured profiles. Revocation is checked at the next tool boundary; it is not an interrupt of a provider already executing.

## MCP journey

| Tool | Purpose |
| --- | --- |
| `ncp_board_status` | Discover bound Board, editable targets, grant limits and retained status. |
| `ncp_board_preview` | Submit base Board reference, node, parameter and replacement; retain candidate preview and affected/unaffected nodes. |
| `ncp_board_validate` | Validate the retained preview and return its validation reference. |
| `ncp_board_baseline` | Explicitly establish the baseline under the selected grant with an attempt ID. |
| `ncp_board_recompute` | Bind preview, validation and baseline references to an execution attempt. |
| `ncp_board_compare` | Read the retained comparison for the resulting candidate. |
| `ncp_board_candidate` | Read the candidate's retained review package. |

For the demonstration, use `example-preview.json` as the preview arguments. Pass the exact returned references to later tools. Preserve an attempt ID when retrying an ambiguous call. To intentionally request a different execution, use a new attempt ID within the remaining host budget. No tool accepts a filesystem path or shell command.

The returned comparison describes numerical deltas and reused/rerun node identities. It does not score physical accuracy, approve the changed model, or update the baseline. Review and any subsequent promotion remain separate host actions.

## Verified protocol boundary

The official Python MCP SDK 2.3.0 client journey currently negotiates MCP `2025-11-25`. It exercises three independent stdio server processes, preserving the same profile, baseline, validation and candidate between them. This increment does not claim `2026-07-28` protocol conformance or remote custom-connector qualification.

Only one process may write a retained profile at a time. Use the connected MCP status tool while its server is active; stop that server before opening the same profile through another launcher. A persistence failure closes the host until its durable checkpoint is reopened. A completed computation without a committed outcome remains unknown and is never silently repeated.

The integrated candidate also qualifies this journey against MCP 2026-07-28.
See [combined installed acceptance](NCP_COMBINED_ACCEPTANCE.md) and
`scripts/check_board_refit_mcp.py`; each protocol retains its own profile and grant.
