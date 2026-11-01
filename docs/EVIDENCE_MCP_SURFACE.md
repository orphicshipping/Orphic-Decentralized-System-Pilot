# Evidence MCP surface

This is the tool contract for the evidence ledger. It does not replace the workbench and it does not execute unbound providers.

`ciw.agent_mcp` remains the local stdio profile over Session, experiments, and registered instruments. Thermal assessment, temperature processing, proved heat, and the polymer kernel stay there. This surface advertises them and refuses to pretend they ran.

## Protocol

Tools-only Model Context Protocol, versions `2025-11-25` and `2025-06-18`.

Methods: `initialize`, `ping`, `tools/list`, `tools/call`. No sampling, no subscriptions, no shell.

## Store

[data_system/0002_evidence.sql](../data_system/0002_evidence.sql) is synthetic sample evidence, not a live rate sheet and not a booking file.

Observations keep partial fields. Stamps keep source, hash, frame, units, uncertainty, and review status. Executions and results are separate rows. A repeated attempt returns the prior result. A different request under the same attempt is refused. Replay writes a new occurrence. An attach refuses to overwrite an existing observation.

## Tools

| Tool | What it does |
| --- | --- |
| `net_capabilities` | Advertised tools, bound operations, and operations that stay unbound. |
| `net_inspect` | Read one investigation and its observations. No rerun. |
| `net_observe` | Page observations. Missing fields stay missing. |
| `net_extract` | Copy one dotted path out of a retained result. |
| `net_source_context` | Return the stamp. A hash is not physical origin. |
| `net_check_edit` | Report a field as present, partial, or absent. Never writes. |
| `net_candidate` | Retain a note. Does not execute. |
| `net_execute` | Run one bound operation. |
| `net_replay` | New occurrence of a retained execution. |
| `net_compare` | Compare one field. Mismatched units are not coerced. |
| `net_qualify` | Arithmetic and decision checks. PASS is not verification and not a booking. |
| `attach_observation` | Insert a new observation and stamp. |
| `inspect_lineage` | Relationships touching one entity. |
| `render_view` | Visualizer payload from retained records only. |

## Bound operations

| Operation | Sample result |
| --- | --- |
| `freight.margin.v1` | SHA–YYZ spread 1360 USD, `candidate_only` (bunker missing, rail space unconfirmed). RTM–YYZ spread 1160 USD, `executable_candidate`. FRA–ORD spread 1280 USD, `executable_candidate`. |
| `freight.routes.v1` | Retained buy legs. A leg list is not confirmed capacity. |
| `analysis.stats.v1` | Cavity mean of three samples. The fourth sample stays missing. |
| `polymer.tolerance.v1` | 12.42 mm against 12.30 ± 0.10 mm. Numerical FAIL. Not a part release. |

## Unbound

`measured-thermal.assess.v1`, `metrology.temperature.process.v1`, `ciw.proved-heat.v1`, and `polymer.injection.assess.v1` are named by `net_capabilities` and refused by `net_execute`.

A margin, a route list, a PASS, or a view does not book freight, admit evidence, or release a part.
