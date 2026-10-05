# NET Polymer Microfab + Discrete Manufacturing Runtime

## Target and implemented boundary

The target is a polymer microfab coupled to discrete part manufacturing, not just
a pilot molding cell. NET remains the general scientific/evidence substrate.

```text
Materials / process development <--> repeatable discrete manufacturing

requirement -> part revision -> BOM -> BOP/route -> work order
            -> material/tool/machine configuration -> cycles -> serialized parts
            -> inspection -> assembly -> packaging -> RELEASE BOUNDARY
```

This first increment implements **offline declarations, configuration checking,
record replay and genealogy**. It does not commission a microfab, execute an
experiment, run a machine, issue a certificate, or release a shipment.

| Layer | Implemented in this increment | Not yet implemented here |
| --- | --- | --- |
| Polymer microfab | Grade/lot, characterization/drying references, moisture interval, declared thermal/process limits, exact configuration-bound study references | Characterization acquisition, formulation, rheology/PVT fitting, crystallization, degradation, DOE execution and automatic model promotion |
| Manufacturing cell | Machine/screw/tool/revision/cavity identities, declared compatibility sets, scalar envelope intersection, time-bound preflight records | CAD-to-runner compilation, industrial protocol drivers, sensor synchronization, state estimation, robot or PLC actuation |
| Discrete manufacturing | Part revisions, counted component BOMs, bounded BOP routes, work orders, start allowances, raw-material allocation, serials, inspection, scrap, assembly consumption, packaging, two-way genealogy | General scheduling/MRP, multi-site inventory, multi-lot blending, rework/disassembly, authenticated release, certificates and shipping integrations |

The discrete object semantics are reusable. The current request orchestrator is
intentionally bound to one injection-molding configuration; additional process
profiles require explicit adapters, not a claim that every process already runs.

## Files and entry points

- `manufacturing_contracts.py`: bounded JSON, explicit SI units, identities and atomic create-only files.
- `polymer_microfab.py`: material/machine/tool/recipe declarations and preflight evaluation.
- `manufacturing_runtime.py`: catalogs, routes, work orders, event replay, inspection and genealogy.
- `manufacturing_example.py`: authored synthetic component-to-assembly example.
- `polymer_manufacturing_cli.py`: example, plan, run, demo, verify and trace commands.
- `polymer_cli.py`: a small `net polymer manufacture` dispatch addition; existing commands remain unchanged.

The new modules reuse `ciw.core.identities` without modifying its encoder or event
identity rules. They do not create a second canonical State Ledger owner. The
local replay receipts are not State Ledger admissions or existing NET Session
verification occurrences.

From an installed repository containing this change:

```sh
net polymer manufacture example --output manufacturing-request.json
net polymer manufacture plan manufacturing-request.json --at-s 1
net polymer manufacture run manufacturing-request.json --output manufacturing-report.json
net polymer manufacture verify manufacturing-report.json --output manufacturing-verification.json
net polymer manufacture trace manufacturing-report.json --serial a001 --output assembly-genealogy.json
```

The directly tested source entry point is:

```sh
PYTHONPATH=src python -m ciw.polymer_manufacturing_cli demo --output manufacturing-demo.json
PYTHONPATH=src python -m ciw.polymer_manufacturing_cli verify manufacturing-demo.json
PYTHONPATH=src python -m pytest -q tests/test_polymer_manufacturing.py
```

Use new output filenames: existing files are never replaced. `plan` evaluates
only the configuration/preflight projection of a request and does not replay its
production events. Exit 2 means an infeasible or insufficient-evidence plan;
exit 1 means a malformed input, refused transition or publication failure. Exit
0 means completion of the requested **software action**, not production release.

## Configuration and qualification semantics

A configuration binds a specific part revision, grade/lot and drying history,
characterization references, press/screw, tool/CAD revision and cavity set, recipe,
and prior development-result references. Its entire content identity is bound
into preflight, forming orders and cycle records. A change invalidates old
bindings rather than silently carrying qualifications across revisions.

The compiler returns:

| Result | Meaning |
| --- | --- |
| `CANDIDATE` | The implemented declared constraints and preflight records passed |
| `INFEASIBLE` | At least one implemented constraint or declared preflight result failed |
| `INSUFFICIENT_EVIDENCE` | A required declaration, uncertainty, reference, compatibility set or current check is missing |

`CANDIDATE` is not `QUALIFIED`, `ARMED`, `SAFE` or an execute grant. A syntactically
valid evidence hash establishes neither that an external artifact exists nor that
it was measured or authenticated. Referenced external artifacts are not resolved
by this first increment.

Temperature ranges are intersected across the material, machine and tool. Shot
volume uses an explicit volume range, not an implicit conversion from a nominal
polystyrene shot mass. Pressure and residence-time requirements retain separate
bounds. The clamp screening calculation is:

```text
required clamp force = TOTAL projected area x declared cavity-pressure bound x declared factor
```

The projected area declaration must already cover the relevant cavities and
runner contribution. Injection pressure is not substituted for cavity pressure.
This scalar screening calculation is not a clamp-mechanics analysis or proof of
safe tool operation. The profile explicitly assumes the declared cavity-pressure
bound is no greater than its injection-pressure setting; it does not model
transient pressure excursions.

The whole declared expanded-uncertainty moisture interval must satisfy its upper
bound. Unknown uncertainty stays unknown. Decimal arithmetic prevents tiny
positive uncertainties or clamp overages from disappearing at a comparison
boundary. This is arithmetic on supplied decimal representations, not a statement
about the accuracy or coverage of the underlying measurements.

Preflight requires separate records for material identity, drying, machine setup,
tool setup, coolant, sensors and independent safety review. Each record retains
its source semantics, evidence reference and validity interval. Every recorded
cycle rechecks the preflight at its own event time. An expired or missing record
cannot become a pass. Recording a safety-review reference is not a live safety
interlock or authentication of that review.

## Discrete event rules

Parts are content-addressed by complete revision records, including requirements,
BOM and inspection plan. Routes and work orders bind those exact revisions.
Conflicting records with the same part number/revision are rejected. BOM links
must resolve within the supplied catalog; counted component multiplicities are
checked at assembly.

V1 BOP routes are deliberately finite: `form -> inspect` or `assemble -> inspect`.
Work orders distinguish the requested good quantity from the maximum permitted
starts. Scrap therefore cannot silently justify unlimited replacement production.

Supported events are `cycle`, `inspection`, `scrap`, `assemble` and `pack`.
They are replayed in nondecreasing time on one declared clock. Event and cycle
identities are unique; serials are never reused. Each cavity can produce at most
one listed output in a cycle. Raw-lot identity, configuration identity and order
route must agree.

For raw-material accounting, issued mass is allocated exactly to the listed part
masses plus process waste. Stock cannot become negative. Decimal mass strings in
the report preserve small deductions from large balances. These are exact
**allocation-ledger** checks, not calibrated physical mass-balance validation.
Scrapped finished parts remain in genealogy and are not automatically converted
into reusable raw resin. No regrind, blend or recycling assumption is made.

Inspection checks every required characteristic against its specified condition
and unit. The full supplied expanded-uncertainty interval must fit within the
tolerance for `PASS`. An interval wholly outside is `FAIL`; an overlap or absent
measurement, uncertainty, evidence or calibration reference is `INDETERMINATE`.
This does not establish simultaneous statistical coverage across characteristics.
An unavailable/failed/indeterminate component cannot be assembled or packaged.
Reinspection cannot overwrite an earlier disposition; explicit rework is deferred.

Assembly consumes the exact BOM counts and records parent/child links. Components
cannot be consumed twice, scrapped after consumption or separately packaged once
consumed. The assembly requires its own inspection. Packaging does not release
anything for shipment.

## Coupling the two loops

A development-run record binds a study, hypothesis, model, evidence and result
reference to the exact tested configuration. A subsequent configuration can cite
that result in `development_refs`; it receives a new configuration identity and
must obtain new preflight bindings. Results are not automatically promoted.

Each formed serial retains the material lot, drying reference, machine/screw,
tool revision, cavity, cycle evidence reference, prediction reference and the
**declared** actor/authorization reference. Genealogy queries return ancestors,
descendants and relevant event records. Pressure/temperature trajectories,
inspection inputs and predictions can be located by their retained references;
this increment does not fetch those external artifacts or compute simulation
residuals from them.

The next substantive integration should resolve and validate existing NET polymer
cycle/measurement artifacts against these identities, then add characterization,
rheology/thermal providers and bounded experimental proposals. No unreviewed
study, model fit or optimizer output should promote itself into production.

## Authority and integrity

Every output retains these boundaries:

```text
hardware_actuation       = not_performed
physical_qualification   = not_established
state_admission          = not_performed
operator_authentication  = not_established
shipment_release         = not_authorized
```

Observe, recommend, authorize, execute and verify remain separate capabilities.
There is no `ship`, `execute` or experimental safety-bypass event. Hardware safety
and interlock authority must remain outside this offline reducer; no software
preflight is presented as a replacement for them.

Reports retain the original request, its content identity, the derived result and
its identity. Event receipts form an ordered hash chain. Fresh verification
recomputes the result and rejects both ordinary corruption and a changed result
whose hash was merely recomputed. Evaluation and verification occurrence IDs are
distinct from deterministic content IDs. Verification uses the same reducer; it
is **not an independently implemented manufacturing verifier**. A coherent rewrite
of all source declarations remains unauthenticated, even if it replays correctly.

## Bounds, tests and remaining validation

V1 supports one configuration and raw lot per request, up to 128 part/route/order
records in each catalog, 128 cavities, 512 serials and 512 events. JSON input and
output are bounded to 2 MiB, with traversal/depth budgets. Duplicate JSON keys,
unknown contract fields, implicit unit aliases, booleans in numeric fields,
nonfinite values and decimal tokens underflowing to binary zero are refused.

Files are published using a same-directory temporary file and an exclusive hard
link. Publication failure leaves no partial destination; unsupported hard-link
filesystems fail closed. This is not a distributed transaction system or a
concurrent multi-user ledger. Parent-directory trust and deployment filesystem
policy still require review.

Local qualification: **179 passed, 1 skipped** under CPython 3.13.5/Linux and
pytest 9.0.2. The skip is the real existing polymer dispatcher test because the
focused connector snapshot lacks unrelated NET dependencies. In a complete
checkout that test imports and invokes the unmocked dispatcher. The standalone
module lifecycle, mutation/refusal tests, provenance bindings, decimal boundary
checks, quarantine, BOM consumption, genealogy and output failure handling ran.

The full repository could not be cloned in this environment. Existing source was
read through the GitHub connector and reconstructed dependencies were checked
against Git blob identities. Full-repository regression, installed-wheel journey,
Windows checks, hosted CI and all physical qualification remain unverified. No
existing chemistry/physics provider, license, dependency pin, allowlist or physical
execution authority is changed by this patch.

## Industrial-interface direction, not implemented conformance

EUROMAP describes 77 as an IMM-to-MES data-exchange interface covering central
information collection, quality assurance, and job/dataset management. Its general
types build on EUROMAP 83. That supports keeping job/part genealogy above the
machine adapter rather than conflating a machine cycle with a production order.
The official EUROMAP navigation separately identifies robot and peripheral-device
interfaces. No OPC UA/EUROMAP driver or conformance claim is included here.

Source inspected 2026-10-05: EUROMAP, “EUROMAP 77,”
<https://www.euromap.org/euromap77>.
