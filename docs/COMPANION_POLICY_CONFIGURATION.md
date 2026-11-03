# Scientific companion policy bindings

The local completion candidate passes an operator-selected `ProcessPolicy`
through scientific and metrology hosts, command-line launchers, execution and
replay. It requires the existing Bubblewrap isolation gate. This plumbing does
not qualify the current host, publish the candidate, or authorize a provider.

| Operator workflow key | Exact provider roles | Host entry points |
| --- | --- | --- |
| `investigation` | `rci`, `fsrt` | Investigation create/replay; `bind_fsrt` |
| `covariance` | `jspt` | Covariance execute/replay; `bind_jspt` |
| `geodesic` | `gte` | Geodesic create/replay; `bind_gte` |
| `calibrated-window` | `tbrt`, `mcur`, `stfe`, `gsie`, `set` | Workbench, scientific API, operational/metrology hosts |
| `residual-monitor` | `fdir`, `oit` | Workbench, scientific API, operational/metrology hosts |
| `bim-clearance` | `cse` | Clearance `Provider` and metrology launcher |

Other native workflows and the separate operational HTTP launcher do not gain
policy plumbing from this patch. A path through them that lacks the required
policy remains held; a configured upstream window does not qualify an entire
acquired-stream workflow.

## Operator configuration

`ciw.companion_policy_config.load_companion_policies()` accepts one absolute
operator-owned regular file, at most 65,536 bytes, without group/other write
access. A symlink at the selected file, duplicate JSON keys, nonfinite numbers,
unknown fields, unknown workflows and missing/extra roles are refused. No
configuration path is taken from a request, investigation, workspace or receipt.

The closed schema is `ciw.companion-policies.v1`, with a `workflows` object.
Each configured workflow contains all its exact roles. Each role requires:

- `read_only_roots`: explicit real directories, including the exact resolved
  provider checkout and its necessary runtime/dependency/Git metadata closure;
- `prlimit_executable` and `expected_prlimit_sha256`;
- `bwrap_executable` and `expected_bwrap_sha256`.

Optional fields are `wall_seconds`, `max_input_bytes`, `max_output_bytes`,
`cpu_seconds`, `memory_bytes`, `file_bytes`, `max_open_files` and `max_processes`,
within the existing [process-policy bounds](../src/ciw/process_policy.py).
There is no `mode` or `environment` field: this loader always selects Bubblewrap
and the fixed minimal environment. It never derives a trusted pin from whichever
binary happens to be installed. An operator must supply reviewed expected hashes.

The following is a template, not a usable qualification record:

```json
{
  "schema": "ciw.companion-policies.v1",
  "workflows": {
    "geodesic": {
      "gte": {
        "read_only_roots": ["/usr", "/opt/odsp/python", "/opt/odsp/providers/gte"],
        "prlimit_executable": "/usr/bin/prlimit",
        "expected_prlimit_sha256": "<reviewed 64-character lowercase hexadecimal digest>",
        "bwrap_executable": "/usr/bin/bwrap",
        "expected_bwrap_sha256": "<reviewed 64-character lowercase hexadecimal digest>"
      }
    }
  }
}
```

Linked checkouts may require explicit read-only Git metadata/object roots outside
their working tree. The loader does not discover or add those mounts. FDIR imports
OIT within its subprocess, so both role definitions must have identical policy
identities and include both checkouts read-only. This prevents a receipt claiming
OIT resource/environment limits that were not applied during computation.

`--companion-policies /absolute/operator.json` is supported by direct scientific
create/replay commands, `ciw serve`, `net science run/replay`, the operational
stdio/local-call launcher, the clearance run/serve launcher, and
`python -m ciw.metrology_operations`. It is separate from `--config`, `--profile`,
source files, and principal authorization policy. Offline record inspection needs
no companion execution policy. Reopening an operational execution host with
`resume=True`, including its view/export paths, requires the same current host
policy as the retained runtime identity. Existing principal/scientific-operation
grants, source pins and request validation still apply.

Trusted Python callers supply `process_policies={role: policy}` to workflow
factories or scientific/workbench bindings. `OperationalHost` takes an outer
workflow-to-role map. Single-provider constructors take `process_policy=policy`.
Bindings are detached from caller dictionaries. The workbench snapshots paths
and policies under one lock, and saved workspaces never restore these bindings.
An operational host without native policies remains inspectable and marks native
providers unavailable. Catalog readiness is not a successful namespace probe.

## Replay and evidence

New runtime receipts retain policy identity, not launch authority. Exact replay
requires an independently supplied current host policy matching that complete
identity. Missing policy history, changed mounts/environment/limits or changed
launcher pins refuse replay before provider probes. Older records remain usable
for offline inspection; they cannot silently become qualified isolated replays.

The operational registry includes selected native policy identities in its
runtime identity. Existing source-hash checks also change when implementation
files change. Historical resume/replay may therefore refuse across this patch;
the implementation does not preserve a stale source hash or automatically retry
a prior attempt. Preserve original evidence and use the documented explicit
fresh-attempt procedure when appropriate.

Configuration, routing and refusal tests include labelled adapter doubles and
do not establish native numerical correctness. Positive companion tests use
actual pinned fixtures and the real namespace probe. On this host the probe
returns `namespace_probe_refused`, so those tests remain skipped and the native
scientific baseline remains open. The independent hardware collector also remains
held because the installed `lsblk` is not owned by UID 0. No namespace controls,
ownership requirements, privileges, hosted settings or companion sources are
changed by this patch.
