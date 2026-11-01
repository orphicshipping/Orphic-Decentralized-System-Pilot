# Evidence MCP surface

Model-facing contract for the evidence ledger. It does not replace the workbench and it does not execute unbound providers.

`ciw.agent_mcp` remains the local stdio profile over Session, experiments, and registered instruments. Thermal assessment, temperature processing, proved heat, and the polymer kernel stay there. This surface names them and refuses to pretend they ran.

## How a model should call

1. `initialize`, then `notifications/initialized`.
2. `net_capabilities` or `tools/list` once.
3. Read with `resources/read` or `net_inspect` / `net_observe`.
4. `net_execute` with a bound operation id.
5. `net_qualify` on the returned `result_id`.
6. `net_replay` only when a new occurrence is required.

The same attempt returns the prior result. A different request under that attempt is refused. Partial fields stay partial. `candidate_only`, `PASS`, and a view do not book freight or release a part.

## Protocol

Versions `2025-11-25` and `2025-06-18`.

Methods: `initialize`, `ping`, `notifications/initialized`, `tools/list`, `tools/call`, `resources/list`, `resources/templates/list`, `resources/read`, `prompts/list`, `prompts/get`.

No sampling, no subscriptions, no shell.

`tools/list` is static. Each tool carries a JSON Schema, a title, and annotations (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`). Nothing here is destructive.

`net_execute.operation_id` is enum-closed to the bound operations. Investigation ids are not enum-closed, so a newly retained id still resolves.

Prompt `operate` returns the routing text. Argument `focus` is appended and does not grant a call.

## Groups

| Group | Tools |
| --- | --- |
| Discover | `net_capabilities` |
| Read | `net_inspect`, `net_observe`, `net_extract`, `net_source_context`, `net_check_edit`, `inspect_lineage` |
| Decide | `net_execute`, `net_replay`, `net_compare`, `net_qualify` |
| Retain | `net_candidate`, `attach_observation` |
| View | `render_view` |

## Resources

Read-only, no tool round-trip:

- `evidence://investigations`
- `evidence://observations` (id, kind, subject, partiality only)
- `evidence://investigations/{id}`
- `evidence://observations/{id}` includes payload and stamp

List rows are compact. Full observation payloads stay on the item URI.

## Store

[data_system/0002_evidence.sql](../data_system/0002_evidence.sql) is synthetic sample evidence, not a live rate sheet.

## Bound operations

| Operation | Sample result |
| --- | --- |
| `freight.margin.v1` | SHA–YYZ spread 1360 USD, `candidate_only`. RTM–YYZ 1160 USD, `executable_candidate`. FRA–ORD 1280 USD, `executable_candidate`. |
| `freight.routes.v1` | Retained buy legs. Not confirmed capacity. |
| `analysis.stats.v1` | Cavity mean of three samples. The fourth stays missing. |
| `polymer.tolerance.v1` | 12.42 mm against 12.30 ± 0.10 mm. Numerical FAIL. Not a part release. |

## Unbound

`measured-thermal.assess.v1`, `metrology.temperature.process.v1`, `ciw.proved-heat.v1`, and `polymer.injection.assess.v1` are advertised by `net_capabilities` and refused by `net_execute`.
