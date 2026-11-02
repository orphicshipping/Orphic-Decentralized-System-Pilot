# NISE → NET handoff

This bridge closes one missing architectural seam without merging NISE and NET.

```text
scientific intent
    ↓
NISE
candidate inference schematic
    ↓
operator binding plan
    ↓
NET semantic capability compiler
    ↓
ordinary ciw.experiment.v1
```

## Ownership

**NISE owns meaning/relevance:** which entities, evidence, models, constraints and
semantic operations are relevant to the investigation.

**NET owns execution:** parameters, dataflow bindings, dependencies, resources,
acceptance criteria, engine lowering and actual execution.

Therefore NISE cannot specify:

- a provider/engine identity;
- an executable/container;
- NET parameters;
- NET input bindings;
- execution authority.

## Binding plan

The operator supplies `ciw.nise-binding-plan.v1` with one entry per NISE
operation selected for execution:

```json
{
  "nise_node_id": "op.spectrum",
  "node_id": "spectrum",
  "parameters": {"channel": "q"},
  "inputs": {},
  "depends_on": ["statistics"],
  "resources": ["cpu"],
  "acceptance": {},
  "inspection": false
}
```

There is deliberately **no capability field** in that record. The capability is
read from NISE `P_q`, preventing the binding packet from silently changing the
meaning NISE selected.

NET then runs the ordinary semantic compiler, which selects only an explicitly
bound qualified lowering.

The shared NISE data validator checks the complete schematic structure, including
all partitions, edges, selection trace, query, candidate-authority claims and
content identity. The trace must cover every selected node exactly once; an empty
trace for a nonempty schematic is refused even if its content hash is resealed.
The handoff retains its separate maximum of 64 operation nodes and 1–64 operator
bindings. Legitimate `analysis.statistics.v1` and `analysis.spectrum.v1` capabilities
still lower through the existing NET registry.

`query.question` is inert scoping context. It is retained under `query_context`
with `role="inert_scoping_context"` and `execution_instruction=false`; it is never
copied into the semantic graph's mandate. That mandate is the fixed text
`Compile the operator-authored NISE binding plan.` Question text cannot select
an engine, parameters, inputs, dependencies, resources or execution authority.

## Retention

The handoff result retains:

- NISE schematic/query/catalog identity;
- selected NISE operation nodes and epistemic standing;
- NISE unresolved capabilities;
- relevant operation evidence references;
- unselected NISE operation nodes;
- the inert question and explicit contract-only provenance;
- the complete semantic compilation receipt.

The handoff remains `authorizes_execution=false`. It creates an executable
*experiment contract*, but execution is still a separate NET action.

Hypothesized NISE operation nodes cannot be bound in V1.

This legacy handoff accepts a structurally valid, self-consistent schematic and
an operator binding plan. Its `provenance` reports
`validation="structural_contract_only"`, `source_authenticated=false` and
`fresh_recomputation=false`. A content digest does not prove the source catalog,
compiler identity or selection provenance. No catalog or provider is loaded to
recompute selection during this command. Stronger origin/replay evidence belongs
to the separately bound NISE profile and does not make this handoff executable or
add an MCP execution grant. Existing schematic/binding inputs remain supported;
the newly retained context and provenance fields change the resulting receipt
digest, and malformed schematics previously accepted by the incomplete validator
are now refused.

## CLI

```sh
net nise compile-handoff schematic.json binding.json --output handoff.json
```

No provider executes during this command.

The schematic file must be nonempty UTF-8 JSON of at most 65,536 bytes; duplicate
JSON keys are refused before binding the NET registry. The shared SDK validator
also bounds nested structure and canonical serialized content. The binding plan
retains the ordinary NET document loader and its existing bounds. Output remains
create-only: an existing file is never overwritten. The CLI summary reports the
same contract-only provenance as the saved receipt.

## CI and platform evidence

The handoff workflow selects
`orphicsystems/Notations-Inference-Schematics-Engine` at
`81908ddbe817018af5d9887a6bdd9cce3e3eafc6`. It installs NISE in a separate provider
virtual environment and invokes that environment's `python -I -B -m nise.cli`;
NISE is not installed into the NET environment. Its synthetic oscillator catalog
and query exercise real pinned schematic compilation, then the ordinary NET CLI
checks the two existing analytical mappings without running either operation.
The handoff itself still reports structural validation only; upstream compilation
in a CI fixture does not authenticate arbitrary later input files.

The Ubuntu and Windows matrix is retained. A local Linux result does not qualify
Windows; the hosted Windows run remains a separate evidence requirement. The
cross-repository checkout also remains a hosted access gate: `contents: read` and
`persist-credentials: false` are unchanged, and no additional token or credential
is supplied. NET's `contents: read` permission does not itself grant access to
another private repository. If the provider is private and unavailable to the
workflow identity, checkout remains blocked pending separately authorized access;
that gate is not a pass.
