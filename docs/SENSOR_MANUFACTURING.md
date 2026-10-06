# Executable sensor manufacturing planning

`engineering.manufacturing-plan.v1` compiles an identified product, its original
native files, BOM, process DAG, resource declarations and inspection matrix into
one retained NET planning result. This extends the existing sensor-project
source, CapabilityRegistry, OperationRegistry, payload validation and Session.
It is not a second manufacturing runtime, measurement schema or genealogy store.
The existing polymer/discrete manufacturing reducer and GaugeLink remain unchanged.

## Implemented boundary

The operation expands the declared BOM by planned unit count, checks process
requirements against explicitly allowed resources, constructs a resource-constrained
whole-lot schedule and associates original native observations with declared
unit/requirement pairs. It retains every input byte through the original file
inventory. Native CAD and PCB files are references, not regenerated or flattened
into another design format. No geometry equivalence or native EDA validation is
inferred from preserving their bytes.

This is executable **planning**: the produced instructions identify operations,
predecessors, resources, adapter labels, original artifacts, batches and modeled
times. It does not synthesize robot trajectories, PLC programs, G-code, electronics
fabrication outputs, purchase orders or physical execute grants. Adapter labels
are declarations, never dynamically imported or called as commands. GaugeLink
supplies measurement evidence; it does not become a controller.

## Commands

Use an explicitly provisioned NET installation containing this operation. The
module CLI is additive and does not change the central `net` command dispatcher.

```sh
python -m ciw.sensor_manufacturing_cli run input-files request.json \
  --output-dir plan-session
python -m ciw.sensor_manufacturing_cli inspect plan-session
python -m ciw.sensor_manufacturing_cli report plan-session \
  --output manufacturing-plan.html
python -m ciw.sensor_manufacturing_cli replay plan-session \
  --output-dir repeated-plan-session
python -m ciw.sensor_manufacturing_cli export-inputs plan-session \
  --output-dir original-inputs
```

All destinations must be new. Put derived reports outside retained Session
directories. `inspect`, `replay`, `report` and `export-inputs` accept an optional
`--expected-snapshot-ref sha256:...` supplied by the operator. The request is the
unchanged `ciw.sensor-project-request.v1` request described in `SENSOR_PROJECT.md`.
The inventory includes a `model.input` artifact named `manufacturing.json`.

Inspection validates an original workspace without new execution. Replay retains
original evidence and inputs while creating a new native execution/result
occurrence. Input export recovers exact bytes and original executable flags.
Recomputation during validation is a consistency check, not an independent
physical audit or cryptographic source authentication. `verification_id` stays
null. The HTML is an offline derived view with no script or remote assets.

An infeasible but well-formed plan is retained with status `INFEASIBLE` and CLI
exit 2. Invalid declarations produce the existing native refusal path; no
successful physical or planning result is invented. Malformed pre-Session source
can refuse before creating a Session. Successful feasible planning exits 0.

## Definition and source contracts

`manufacturing.json` has exactly these top-level fields:

```text
schema = ciw.sensor-manufacturing-definition.v1
product = {product_id, revision, native_paths}
order = {work_order_id, quantity, unit_ids}
requirements_path
bom = {path, columns: {reference, quantity, manufacturer, part_number}}
resources = [{resource_id, kind, capabilities, limits,
              batch_capacity, rate_per_hour, currency}]
operations = [{operation_id, kind, depends_on, artifact_paths, requires,
               demands, allowed_resources, setup_s, per_batch_s,
               batch_size, adapter_id}]
inspections = [{unit_id, requirement_id, operation_id, observation_ref,
                expected_identity, frame, clock_id, required_source_paths}]
basis = declared_planning_assumptions
```

Product and revision must match the original sensor-project declaration. The
order has 1–128 distinct planned unit IDs; no physical birth or authenticated
production genealogy is implied. At least one native PCB or mechanical CAD is
required. Files stay under the inherited 128-file / 2 MiB source boundary.
At most 64 process operations and 32 resources are admitted.

The BOM is an original UTF-8 CSV declared as `manufacturing.output`. Explicit
column mapping is mandatory. Missing/duplicate headers, duplicate designators,
malformed rows and nonpositive or noninteger quantities refuse. Other original
columns remain attached to their rows. Quantities aggregate by exact manufacturer
and part number. There is no inferred substitution, procurement approval, yield,
scrap allowance, component authenticity or purchasing action.

Each resource has `kind` supplier, cell or instrument; explicit capability labels;
1–128 batch capacity; and numeric limits `{parameter, unit, lower, upper}`.
An operation has kind fabricate, procure, assemble, inspect or package. Its
`requires` must be covered by a resource's labels, every numeric demand must fall
within an explicitly supplied envelope with the exact same unit string, and its
batch size cannot exceed the resource's declared capacity. No automatic unit
conversion or inference of missing capabilities occurs.

A demand is `{parameter, unit, value, source}`. `source` may be null for an
explicit planning assumption, or `{path, keys}` for a JSON path into retained
original design bytes. For example, keys `["size_mm", 0]` can bind a declared
width to a source layout field. Values must agree exactly with the source field.
This is typed data lookup, not expression evaluation, native CAD parsing or
arbitrary code execution. It prevents changing a demand without changing its
original declared source; it does not attest the correctness of the source.

## Schedule semantics

The scheduler is deterministic lexical topological ordering followed by greedy
earliest-finish assignment among compatible, explicitly allowed resources.
A resource processes one whole-lot operation at a time. An operation waits for
all whole-lot predecessors. Batches are `ceil(quantity / batch_size)` and modeled
duration is `setup_s + batches * per_batch_s`. A final partial batch takes the
same declared batch time. Resource capacity gates eligibility; it does not create
unmodeled parallel servers or concurrency within an operation.

The result preserves rejected-resource reasons, relative start/finish times and
resource busy time. An incompatible operation blocks its descendants; independent
operations may still be scheduled. Infeasible plans have null total makespan.
No global optimum is claimed. The model does not silently include physical
transport, inventory delay, supplier calendar, failures, rework, yield, stochastic
service time, geometric accessibility or detailed thermal/electrical/mechanical
physics. These require additional explicit models and observations, not a claim
that the present scheduler simulates them.

Optional processing cost uses only declared time and hourly rates. Unknown rates,
mixed currencies or infeasibility prevent a total. Costs exclude components,
freight and unmodeled lead time. No market quotation is retrieved or fabricated.

## Inspection without changing original measurements

The requirements file contains `{product_id, revision, requirements}`. Each
requirement has `{requirement_id, quantity, unit, lower, upper, procedure_path,
criterion_basis}`. Unspecified criteria require null bounds. Other admitted
bases are `operator_declared` and `synthetic_example`. Bounds are nominal only.
Every planned unit/requirement pair needs one entry, including pending entries
with `observation_ref: null`. No observation may be silently reused for two units.

Available observations pass through the existing sensor-project native stream
validator. The association checks original model/entity identity, quantity,
unit, frame and clock. It retains the original observation, uncertainty,
provenance and file mapping; it never fills missing uncertainty or calibration.
`required_source_paths`, when supplied, must actually be referenced by the
original observation. Empty requirements mean no such source-reference assertion.

The requirement's procedure bytes are always retained as planning inputs. If its
hash appears in the original observation's source references, the report says
`ORIGINAL_SOURCE_REFERENCE`. Otherwise it says `PLANNING_ASSOCIATION_ONLY`—this
is important for legacy GaugeLink projections that do not flatten procedure
references into the native observation's source list. A new association cannot
retroactively change how an old measurement was acquired.

Nominal comparison can be PENDING, CRITERION_UNSPECIFIED, NOMINAL_WITHIN or
NOMINAL_OUTSIDE. In every case qualification remains NOT_ESTABLISHED and acceptance
NOT_AUTHORIZED. Unit association remains operator-declared, not authenticated
genealogy. A nominal comparison is not a quality-release decision, nor does it
establish completion of an inspection step in the manufacturing reducer.

## Verification and demonstrated design investigation

The 60 new tests use genuine NET Session execution, source reconstruction and
native observation validation. They cover malformed definitions and BOMs,
revision and source-field binding, process cycles, capability/envelope and batch
limits, resource contention, partial batches, unknown/mixed costs, complete
inspection coverage, original-observation identity/procedure/clock binding,
nominal comparisons, replay identities, no-clobber and CLI execution. Test CAD
fixtures are deliberately synthetic placeholders, not valid native EDA designs.
The 113 inherited sensor-project cases remain unmodified.

A separate private handoff uses the user's actual retained MEMS A1 native files,
BOM/CPL, 10 x 10 mm layout declaration, 0.15 mm clearance declaration and original
release records. It does not publish those product designs in generic NET source.
For 12 planned units the seven-component BOM expands to 84 component positions.
With authored timing and capability assumptions, a one-site inspection route
models 1,490 seconds; a proposed four-site route models 1,130 seconds. This
360-second difference is a scenario calculation, not measured production saving
or proof that a four-site fixture works. Changing only batch size without its
resource capacity refuses. A fabricator with a 0.20 mm lower clearance limit is
incompatible with the original 0.15 mm source-bound demand.

A separate legacy GaugeLink association preserves a historical synthetic
24.982 mm observation and unknown uncertainty. It is not a new physical capture,
not a MEMS measurement, and not an invocation of GaugeLink acquisition. Its
procedure relationship remains explicitly planning-only. Actual MEMS inspections
remain pending; native EDA release gates and physical qualification are unchanged.

Detailed physics models, machine-specific program generation, production-order
admission into the existing manufacturing reducer, authenticated genealogy and
empirical acceptance remain subsequent integration work. This increment delivers
a tested product-to-plan compilation and observation-association boundary without
pretending that the complete automated factory has already been commissioned.
