# Polymer microfab: manufacturing-plan foundation V1

This is the first bounded manufacturing-plan increment, not a complete polymer
microfab, digital twin, material solver, production system, or machine controller.
NET remains the general substrate. Existing polymer cycle evidence and execution
contracts are unchanged. This module reuses `ciw.core.identities` for canonical
content identity; it creates no Session, execution, verification occurrence, or
canonical-state admission.

## Operator journey

From an installed checkout containing this change:

```sh
net polymer microfab example --output plan.json
net polymer microfab compile plan.json --output envelope.json
net polymer microfab verify plan.json envelope.json
```

The direct module entry point is equivalent:

```sh
python -m ciw.microfab_cli example --output plan.json
python -m ciw.microfab_cli compile plan.json --output envelope.json
python -m ciw.microfab_cli verify plan.json envelope.json
```

Use fresh output paths: publication is create-only. The example is entirely
synthetic. Its numerical values are authored test fixtures, not material-grade
recommendations, experimentally qualified settings, or an executable recipe.
V1 accepts bounded JSON, not YAML. JSON syntax, duplicate keys, nonfinite numbers,
underflow during numeric parsing, direct input symlinks, and byte budgets are
checked. Local file operations assume a trusted filesystem; this is not a
race-proof hostile-filesystem boundary.

## What is bound

A plan binds one facility, a part and revision, a material and revision, a machine
capability declaration, a tool and individually named cavities, a quality-process
constraint declaration, a material lot and its declared disposition, and a work
order with quantity, route/revision and operation identity.

Each component requires an ID, revision and canonical SHA256 source reference.
The compiler checks both ID and revision across part/material, lot/material,
tool/part, quality/part and order/part relationships. Source references are retained
identifiers, not proof that a document was acquired, authenticated or assessed.
The complete plan and each complete component receive separate content identities.
Changing any component or source reference changes its binding. These are content
identities, never manufacturing-cycle occurrence IDs or signed attestations.

There is no executed-part genealogy yet. Work-order quantities and cavity IDs
are retained declarations; no production count, material reservation, dispatch,
BOM/BOP execution, assembly, inspection, shipment or release occurs.

## Declared constraint screening

The compiler intersects closed intervals in exact declared SI units, without
conversion, optimization, model fitting, or extrapolation:

| Axis | Unit | Required declarations, in addition to the requested range |
| --- | --- | --- |
| Melt temperature | K | Material, machine, tool, quality |
| Mold temperature | K | Material, machine, tool, quality |
| Injection pressure | Pa | Machine, tool, quality |
| Residence time | s | Material, quality |

Every required field must exist. An explicit `null` means unknown; it never
means an unconstrained or infinite capability. A missing field is malformed.

`CANDIDATE` means only that the identities match, the lot is declared AVAILABLE,
and all four supported interval intersections are known and nonempty. It does
not establish that the tool fits the machine, the shot or clamp capacity is
sufficient, the resin is usable, or the part can actually be molded.

`HOLD` means a required range is unknown or the lot is held. Unknown axes have no
published envelope. `REFUSE` means a declared range conflict, mismatched binding,
or quarantined lot. A known conflict takes precedence over an unrelated unknown.
Known nonconflicting axis intervals may remain in a HOLD/REFUSE report for
inspection; they are not permission to use the overall plan.

The compiler does not derive filling, packing, cooling, moisture, degradation,
viscosity, shrinkage, runner balance, tool wear, dimensional quality or safety.
Temperature/pressure interval checks are not proof of a safe operating region.
Real hardware must remain behind separately reviewed authorization and safety
systems. No hardware interface exists in this increment.

## Retained reconstruction and authority

`verify` freshly revalidates the plan and reconstructs the entire envelope. It
rejects edited or coherently resealed results. This is deterministic reconstruction
using the same compiler, not an independent numerical or scientific verifier.
A reconstruction PASS can accompany a HOLD or REFUSE envelope; both statuses
remain visible and the CLI exits 2 in those cases.

Exit codes: 0 for a created example, candidate compilation or reconstructed
candidate; 2 for a valid HOLD/REFUSE outcome; 1 for invalid input, reconstruction
mismatch or file-operation failure. Argument syntax errors use argparse's exit 2.

All outputs preserve explicit no-authority fields for source authentication,
physical validation, calibration traceability, process qualification, production
release, state admission and hardware actuation.

## Validation and next boundary

```sh
PYTHONPATH=src python -m unittest discover -s tests -p test_microfab.py -v
```

The focused suite covers deterministic identity, detached outputs, missing limits,
unit mismatch, changed revisions, material holds/quarantine, interval conflict,
closed-boundary intersections, hostile JSON and create-only publication. An
independent finite-grid oracle checks 200 seeded integer-interval scenarios.

This lands a P0 identity foundation and a limited P4 declaration compiler. Remaining
work includes material-state physics, geometry and machine-capacity checks, the
virtual polymer cell, native Session/operation dispatch, telemetry, immutable
cycle/serial genealogy, physical evidence and separately authorized release.
