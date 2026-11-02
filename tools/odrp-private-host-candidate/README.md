<!-- ncp-folder-entry -->
# MCP entry

- path: `tools/odrp-private-host-candidate`
- class: `navigation`
- authorizes_execution: false
- call: `ncp_index_list`

This folder does not opt for the easiest task in a set. Name the task.

Tasks in this folder, none of them the default: `tools`.

The contract is [MCP.md](MCP.md). A file in this folder is not a tool call. This folder is not a human console. Reading this page does not run a solver, bind a provider, or qualify a result.

---

# ODSP / ODRP isolated private-host candidate

This is a source-only branch candidate, not a deployed Cloudflare update.
The branch-scoped build-only workflow builds the wheel from the exact checked-out
revision and assembles this context from existing root console/prepare/helper
sources, avoiding checked-in binary wheels and duplicate engines.

## Premise → requirement → decision
Models and programs need existing bounded execution and pinned source access.
Reuse the installed ODSP wheel and existing scientific MCP bridge; add ODRP
read-only shared-service dispatch without giving reference data execution grants.
Preserve the seven HTTP methods/paths, bearer authorization, browser-origin
refusal, bounded bodies, confirmation rules and unknown-outcome fences.
A different container version must never silently reopen historical uncertain
state: this candidate uses a distinct singleton name and checkpoint generation.

## Contents and local operation
- wheel/: created by CI from the current branch source; not checked in.
- tools/private-mcp-host/: candidate adapter, Worker and unchanged-path gateway.
- tools/mcp-operator-console/: existing MCP client, not a replacement engine.
- examples/numerical_operations/: existing seven synthetic operation profile.
- Historical source remains in tools/private-mcp-host at the repository root.
- Local candidate acceptance: 8 Python adapter tests and 7 Node boundary/fence tests; not cloud qualification.

Build context is this directory. Dockerfile retains the pinned upstream Python
3.12 image and existing build-time proxy-CA secret requirement. No Docker engine
is available here: the image has NOT been built or deployed. A wheel install in
an isolated Python 3.12 environment was tested, not a container image.
Use a separately approved private staging application and separate Durable Object
storage/R2 prefix before any rollout. Do NOT bind this candidate to the deployed
ncp-control singleton/application or overwrite its image. The gateway hostname
is intentionally still the old compatibility hostname; no route/config is ready
for deployment. A staging hostname/auth policy must be approved and bound first.
Do not create JWT/registry credentials or billable resources from this package.

Runtime setup (operator, never a tool argument):
- NCP_TRANSACTION_STATE: absolute disposable directory ending in /state.
- ODRP_CATALOG and ODRP_CATALOG_REF: approved local immutable metadata bundle/pin.
- Optional ODRP_EVIDENCE and ODRP_EVIDENCE_REF: approved local evidence bundle/pin.
Absent all ODRP variables, reference tools are neither exposed nor callable.
No production corpus is bundled; tests compile synthetic company-neutral text.
Reference operations share services.invoke and the existing KnowledgeHost schemas.
Scientific execution remains actual MCP. Reference HTTP dispatch is shared SDK,
not a new MCP-over-HTTP transport; existing reference stdio remains available.

Launch after approved installation with original requirements:
  python -m uvicorn adapter:app --app-dir tools/private-mcp-host --host 127.0.0.1 --port 8080 --no-access-log
The container uses 0.0.0.0 internally only, behind the existing Worker boundary.
/health is process readiness/version metadata, not corpus/runtime qualification.
GET /api/status is a transaction and may checkpoint: not a read-only health probe.

## Recovery and migration hold
The old deployed c47a862… host and image 5a28412… are not this candidate.
Historical outcome_unknown transaction 4cbd9869… remains unresolved. Preserve its
DO pending marker, committed pointer and R2 checkpoint objects; never replay,
clear the fence, reset, initialize from null, or automatically migrate that state.
An isolated candidate accepts only its own exact generation-marked checkpoints.
Malformed/historical archives refuse before deleting the existing local state.
The Worker tests emulate Durable Object storage; they are not Cloudflare runtime
or transactional-storage qualification. A successful local test does not settle
an unknown historical outcome.

Before approved deployment: review exact image/dependency receipts, verify
container build, confirm isolated private storage and auth, provision only
approved mounted bundles, perform synthetic staged journey and restart recovery,
and obtain a separately approved evidence-based historical migration decision.
No automatic cross-generation importer is provided because conversion cannot
resolve an unknown transaction outcome. All scientific fixtures stay synthetic.

## Proposed CI qualification (not published or run)
.github/workflows/odrp-private-host-candidate.yml reuses existing system-composition workflow's
pinned checkout action, read-only permission and 25-minute Ubuntu image gate.
It runs only on the approved candidate branch push or manual dispatch and builds a local image without registry login/push; its
Dockerfile.ci uses normal package TLS (no invented proxy secret) and installs
only the test dependencies needed for the candidate. Container execution has
network disabled, read-only root, disposable /tmp, and no mounted corpus/secret.
It runs the scientific + reference adapter tests inside the actual image, then
local Node gateway/fence simulations. Workflow presence is not a CI result. Consult the exact commit run before claiming container qualification.
