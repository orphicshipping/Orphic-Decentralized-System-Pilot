# Principal-bound operational metrology

`ciw.metrology_operations` connects existing native metrology jobs to the current
principal facade, durable attempt tracking and review. It adds a usable MCP
journey: inspect an exact plan and its original source/calibration context, run
that plan, reconnect to the retained outcome, read its native artifacts, and
append an explicitly separate review. It uses the existing scalar,
calibrated-window, residual-monitor and BIM-clearance engines; it defines no new
uncertainty, geometry, calibration or diagnosis calculation.

The implementation extends the retained-record and reconnect requirements in
[the operational work order](work-orders/WO-NCP-OE-MCP-20261017.md). Existing
[operational investigations](OPERATIONAL_ENGINEERING.md) still own acquisition,
configuration comparison, experiment proposals, supervisory review and command
authorization. This profile does not duplicate those operations or turn their
local qualification into physical qualification.

## Fixed jobs and current policy

A trusted launcher opens each existing `OperationalHost` or `ClearanceHost`, then
constructs `MetrologyJob.operational(host, model_alias)` or
`MetrologyJob.clearance(host)`. The job retains its exact native source, fixed
graph, host policy, original source bytes, calibration context and plan digest.
Source/provider paths, operation graphs, identity and policy are never tool
arguments. The current host policy and original source/graph/profile digests are
checked again before use. Provider source/runtime verification remains in the
existing native dispatch path.

```python
from ciw.metrology_operations import MetrologyHost, MetrologyJob

metrology = MetrologyHost(
    policy=existing_principal_policy,
    principal="operator",
    tenant="lab",
    jobs={"force": MetrologyJob.operational(existing_operational_host, "scalar")},
    journal="/absolute/host-owned/metrology.sqlite",
    create=True,  # Explicit one-time creation. Reopen with create=False.
)
facade = existing_principal_policy.facade("operator", metrology=metrology)
```

The launcher owns each native host's context manager for the entire service
lifetime. Reopening requires the same journal identity, tenant, principal,
plan digests and native output stores. `binding_scope()` contributes hashes of
those bindings, original sources and native runtime catalogs to the shared
context selector. It exposes no paths or credentials. `profile_manifest()`
registers the existing five schemas in the shared capability map.

Every call independently passes current principal operation/argument rules.
Every new run also requires **all** outer and nested scientific operations:

- Scalar: the existing operational assessment/verification operations and all
  four native Link decoding, affine-calibration, shared-covariance mean and audit
  operations.
- Calibrated window: operational assessment/verification, TBRT, MCUR, STFE and
  GSIE window operations, plus `mcur.affine-first-order.v1`.
- Residual monitor: operational assessment/verification and
  `ciw.residual-monitor.v1`, which retains the original FDIR/OIT boundary.
- BIM clearance: `metrology.bim-clearance.v1` and the unchanged fixed graph.

The exact names are returned by the job plan and catalog. This list describes
requirements; it does not install real grants. `services.invoke` accepts this
tool family only through the current principal/audited path. A disabled
principal, changed argument constraint, revoked investigation or missing nested
scientific grant refuses the request. Changes during durable admission are
checked before native dispatch. Once native work has begun, later revocation
cannot undo that occurrence; subsequent disclosure still checks current policy.

For a durable audit requirement, wrap the facade in the existing
`AuditedPrincipalHost` with its independent anchor. The
[native HTTP gateway](PRINCIPAL_HTTP.md) can bind that wrapper directly. This
profile neither creates an audit anchor nor substitutes review records for one.

## MCP and local launcher

| Tool | Behavior |
| --- | --- |
| `ncp_metrology_catalog` | Lists fixed jobs, plan digests, required scientific operations and provider status. `currently_executable` is a current policy/binding/budget snapshot; dispatch rechecks the native provider pins. |
| `ncp_metrology_run` | Requires exact `job`, `plan_ref` and `attempt`; invokes the existing fixed native graph once for that durable attempt. |
| `ncp_metrology_status` | Reopens an owned attempt, original execution/result identities and review references without provider work. |
| `ncp_metrology_read` | Returns original artifact bytes as bounded base64 pages with a whole-artifact SHA-256 reference. |
| `ncp_metrology_review` | Appends `reviewed` or `requires_followup` against an exact retained outcome. It cannot change a result or grant execution. |

Artifacts include the plan, original source, calibration context, outcome,
native workspace, graph run, experiment and operational finding when available.
A review requires the exact outcome reference, review ID, disposition and note.
The same review ID and content is idempotent; different content under that ID is
refused. Reviews never become calibration approval, equipment acceptance,
canonical state admission or evidence of physical truth.

The CLI consumes one host-owned closed configuration:

```json
{
  "schema": "ciw.metrology-operations.v1",
  "principal": "operator",
  "tenant": "lab",
  "policy": "/absolute/host-owned/principal-policy.json",
  "journal": "/absolute/host-owned/metrology.sqlite",
  "jobs": {
    "force": {
      "kind": "operational",
      "profile": "/absolute/host-owned/operational-profile.json",
      "model": "scalar"
    }
  }
}
```

Operational profiles are prepared through the existing operational admission or
`operational_cli.prepare` path. For BIM, use an existing clearance profile and
the job fields `kind: "bim-clearance"`, `profile`, `provider` and `revision`.
The provider path and its actual full commit are trusted launch inputs. Its tree
must still equal the existing native pin. Each job needs its own native host
output store; a single native host cannot be reused for multiple jobs.

With the current candidate installed and dependencies pinned:

```sh
python -I -m ciw.metrology_operations --config /absolute/host-owned/metrology.json \
  --initialize --tool ncp_metrology_catalog
python -I -m ciw.metrology_operations --config /absolute/host-owned/metrology.json
```

Initialization is explicit and create-only. Omit `--initialize` for reconnects.
For native jobs, also supply `--companion-policies /absolute/operator.json` on
each launch. This separate [operator configuration](COMPANION_POLICY_CONFIGURATION.md)
selects mandatory Bubblewrap policies; the metrology job configuration and tool
arguments cannot select them. Missing native policies leave operational jobs
unavailable, and clearance provider construction refuses. The real namespace
gate must succeed at dispatch; a catalog entry does not qualify isolation.
The second command serves the official MCP SDK over stdio, through the same
principal facade. `--tool NAME --arguments JSON` supports one local call through
that facade. The configuration creates no accounts or credentials and contacts
no instrument, broker, device or hosted service. Production launch must use an
installed package; the source-checkout qualification below does not establish
installed-wheel or container behavior.

## Durable outcomes and bounds

The existing `link_store.Journal` and `operational_store.Records` persist exact
source/calibration/plan bytes, intent, outcome and review records. A Journal
compare-and-set claim precedes native dispatch. Concurrent workflow instances
cannot dispatch the same attempt twice. Native AgentHost retains its own
execution/result identities and restart behavior; the workflow record links to
them without replacing scientific state.

An interrupted claimed attempt, crash or failed post-dispatch retention remains
`outcome_unknown` and cannot be automatically reexecuted. Inspect it before
choosing an explicitly different attempt. A completed retry returns the same
outcome and original identities. Storage refusal never silently evicts evidence
or turns unknown work into completion. Review failures do not alter native
outcomes.

An unknown run returns the standard outer refusal with `error.code` equal to
`outcome_unknown`, so the existing workflow and audit gates cannot classify it
as success. The retained intent remains available to current-policy status
inspection. An audited host additionally keeps its existing unresolved-call
hold until the host performs its explicit custody reconciliation; no metrology
tool can clear that hold.

Bounds are eight jobs, 64 durable attempts per journal, 16 reviews per attempt,
2,048 note characters, 64 KiB strict incoming JSON, and at most 4,096 raw bytes
per artifact page. The existing Journal's record/byte limits and native
AgentHost/provider budgets apply in addition. The CLI uses the principal
facade's strict MCP input path.

## Executed results and remaining qualification

Run the source-checkout tests with the repository's pinned MCP and numerical
dependencies:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests/test_metrology_operations.py -q
```

The local suite exercises actual native scalar calculations through the
principal facade: mean `2.03`, variance `0.00190808`, and the native scalar
arithmetic audit `PASS`. The separate model-support finding remains
`INSUFFICIENT_EVIDENCE`. Original source/calibration bytes survive reconnects.
The synthetic certificate's parser reports `FAILED` and correction authority
remains `NOT_GRANTED`; a numerical result does not repair either.

BIM tests invoke the actual imported native CSE implementation at tree
`f91c982b580ad4f16a27ea149eedf0e338f6d764`, under Python 3.12.14 and NumPy 2.4.3.
The local fixture reconstructs Git metadata for that unchanged tree; it does not
claim an independently acquired upstream commit. The native uncertain inspection
retains `UNRESOLVED`, while the independent-pose disagreement fixture remains
`held`. Those exact native interpretations are visible in workflow summaries.

The suite also uses a real official SDK client across separate stdio processes,
reconnects without a second execution, checks policy revocation and discovery,
tests cross-owner/tenant and cross-attempt substitution refusal, races two
durable claims, and injects crash/retention failure after work. Synthetic fault
injection is labeled as such; native numerical calculations are not mocks.

The calibrated-window and residual-monitor bindings are implemented through the
existing engines, but their native execution was **not run for this addition**.
No prepared exact role stack was found in the available workspace, and prior
qualification records also marked that stack unrun. Missing bindings are
reported as `provider_unavailable` and refuse before the durable run claim. The
required revisions remain those in
[`calibrated-window-runtimes.json`](../src/ciw/calibrated-window-runtimes.json):
TBRT `40507060ca7a9126a9d641999b994a757eef3bfd`, MCUR
`49405ecd623474ddf601989b0a8195be401544e0`, STFE
`54dd43b657b92d87c2fc263dfd99437c664be353`, GSIE
`de38873db1ba98744d395dfa7df1d3b5b7bb3059`, and SET
`2f838f4e196f453efc3a59045b0b3ec4b5680296`. The monitor additionally requires
FDIR `29e4b306492b793487d47a96b97a56548217aa12` and OIT
`db4c564bddbe1911f96585bd18f58659a0026fb7`. Imported source presence alone does
not establish these runtime bindings. No pin was weakened or new stack built
under the current disk-capacity limit.

Physical instrument acquisition, authentic calibration-chain verification,
independent reference measurements, declared joint comparison uncertainty,
installed/container qualification and hosted OAuth remain separate gates. This
addition establishes no physical acceptance or corrective-action authority.
