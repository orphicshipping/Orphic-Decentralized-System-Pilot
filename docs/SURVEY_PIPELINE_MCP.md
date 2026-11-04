# Canonical principal-bound survey pipeline

`SurveyPipelineHost` now exposes the [fixed local survey workflow](SURVEY_ANALYSIS_PIPELINE.md)
through `PrincipalPolicy.facade(..., pipeline=...)`, `services.invoke` and the
existing strict MCP stdio adapters. Registration creates no default host, grant,
listener, token, external source or equipment connection. This is one fixed
plan, not a general SQL/dbt or physical-state execution service.

| Tool | Closed request | Result |
| --- | --- | --- |
| `ncp_pipeline_catalog` | Empty object | Bound plan definition and schema/model/code commitments; discovery explicitly does not assert readiness. |
| `ncp_pipeline_run` | Exact `plan_ref`, bounded `attempt` | Execute or reopen the host-selected plan; compact held summary and artifact/value pins. |
| `ncp_pipeline_inspect` | Same selectors, existing attempt | Current-authorized status; no recovery or resend. |
| `ncp_pipeline_read` | Same selectors plus integer `offset`, `limit` | At most 16 KiB of canonical artifact bytes as base64, with total size, pin and next offset. |

Attempts are 1–80 characters under the fixed identifier rule. Offsets are
0–131072; limits are 128–16384. Booleans are not integers. Checking uses the
existing shared schema validator and a fixed profile identifier check; no global
regex-validation rule is expanded. The plan pin can confirm only the bound plan.
No request can supply SQL, code, paths, endpoints, model/source selection,
principals, policies, grants, callbacks or approval flags. Replies are at most
64 KiB, with the full duplicated MCP envelope checked against the existing
wire budget. A caller cannot select another artifact owner or arbitrary file.

## Authority and admission

The shared canonical request validator runs before dispatch. Current principal
operation grants and argument constraints apply on every call. Execution needs
both `statistics.v1` and `uncertainty.propagate.v1` scientific grants. Inspection
and disclosure retain the underlying pipeline's scientific/source checks.
Catalog is metadata only and does not assert source readiness.

The concrete profile must match the facade's exact policy object and principal,
at construction and dispatch. Profile, tenant, pipeline instance/plan, code and
interface bindings are checked around callbacks and I/O. The explicit service
admission and original pipeline admission each require fresh exact `True`;
either can refuse. Neither can override schema/control/freshness or sticky
source holds. Raw `services.invoke(..., host=profile)` refuses; use the principal
facade or audited wrapper. Even direct trusted-library profile calls check
current operation grants and both admissions.

The profile scope guard participates in existing `AuditedPrincipalHost` barriers
after durable journal/anchor I/O, before dispatch and before disclosure. A source
becoming stale during admission prevents computation. Revocation after a result
or terminal audit commit withholds data and reports uncertainty without erasing
the committed artifact or event. Only this concrete new profile's verified
`not_started` refusal is classified as pre-effect; other profiles retain their
existing classification. No audit field or saved context is a grant.

## Replay, paging and historical identity

A new RPC identifier with the same attempt returns the original artifact without
another SQL transform, calculation, assessment or retention occurrence. Audited
calls still get their own audit occurrences. A trusted audit `call_id` cannot
dispatch twice and is not a tool argument. Unknown native attempts remain
inspectable as unknown, cannot be read as completed artifacts or resumed, and
hold fresh work pending review. Revocation and source holds block saved-result
disclosure and every page. Output remains held for human review, with explicit
conditional/unqualified uncertainty and no state-admission authority.

The native workflow pins principal-policy source bytes, so this integration
creates a new plan identity for newly qualified bindings. It does not migrate,
overwrite or relabel earlier queues or artifacts. Old artifacts keep their
original implementation identities and remain available to pure historical
inspection with a trusted pin; that inspection restores no current authority.

## Trusted launcher and local evidence

```python
from ciw.survey_pipeline_profile import SurveyPipelineHost

# Existing objects and callbacks belong to the trusted operator launcher.
profile = SurveyPipelineHost(existing_pipeline, admit=current_service_admission)
facade = existing_policy.facade(existing_principal, pipeline=profile)
# Use the existing AuditedPrincipalHost and durable chain/anchor where required.
```

Required grants must already exist in the operator's policy. The snippet does
not authorize changes to settings or access. The [synthetic example](../examples/domain-h-survey-mcp/README.md)
creates only its own new test policy, source, queue and local audit records:

```sh
PYTHONPATH=src python examples/domain-h-survey-mcp/run.py \
  --output /private/new-canonical-survey
```

It exercises canonical SDK calls, paged assembly and byte pins. Its redacted
audit chain uses a separate local SQLite CAS head, not a remote trust anchor or
NSL admission. Add `--stdio` for the existing official MCP SDK and strict
raw-frame adapter; no HTTP listener is created. Use an already installed
supported MCP dependency, clean operator environment and new private directory.
No credentials or existing host security-setting changes are needed.

Tests cover real canonical and official MCP SDK stdio dispatch, bounds, hostile
selectors, current grants/constraints, both admissions, drift/staleness,
subject/policy rebinding, audit I/O races, unknown native attempts and replay
without calculation. Fixtures are self-authored synthetic data and declarations.
The independent numeric/custody checks do not establish physical calibration.

The [fixed distributed adapter](DISTRIBUTED_SURVEY_PIPELINE.md) now composes this
principal-bound execution with local PostgreSQL custody and JetStream, preserving
current grants, admissions and source holds. It seals the native run directory
to a coordinator claim and keeps uncertainty distinct from a committed PG result.
Multi-host ownership, complete pipeline cross-store recovery, general SQL/dbt,
signed workflow registration and shared cache remain separate work.
Hosted identity/TLS/OAuth, managed restore/failover,
authenticated TBRT time, NSL admission, company data and physical instruments are
not qualified. The full scientific baseline and hosted/CI gates remain open.
Nothing here publishes, deploys, collects externally or commands equipment.
