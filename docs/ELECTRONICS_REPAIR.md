# Electronics repair workbench

The first workflow imports manually mapped DC voltage/current endpoints for repair,
refurbishment and retrofit cases. It retains original files, calibration declarations,
uncertainty budgets, device revisions, interventions and paired before/after results.
The standalone HTML view works offline. The existing measurement visualizer remains
available separately; this view uses the electronics case schema.

## Run

From an installed package, or with `src` on PYTHONPATH:

```sh
python -m ciw.electronics_repair_demo --output-dir demo-case
python -m ciw.electronics_repair --case demo-case/case.json --output-dir demo-result
```

Open `demo-result/index.html`. Both output paths must be new. Exit zero means
the import completed, not that the equipment passed qualification. The example
is entirely synthetic, including instruments, calibration, limits and intervention.

## Operator intake

Use the example case structure as a schema guide, not as real evidence. Create a
separate case with actual equipment/instrument identities, hardware and firmware
revisions, fixture, conditions and retained files. Set `data_origin` to
`operator_measurements` only for actual measurements. Each evidence descriptor has
`file` (relative to the case directory) and `sha256` (`sha256:` followed by the
file's SHA-256 hex digest). Paths must stay inside the case directory.

Provide original instrument exports and describe their manual mapping. This adapter
does not parse arbitrary vendor exports or independently verify that the mapping
matches them. Calibration certificates and budget documents are retained, not
authenticated or interpreted automatically. Approval identity/time are declarations.

The canonical CSV header, in this exact order, is:

```csv
id,phase,check_id,condition_id,device_serial,instrument_id,calibration_id,budget_id,timestamp,value,correction,unit,standard_uncertainty,source_id,source_locator
```

Every declared check/condition requires exactly one `before` and one `after` row.
Use unique row IDs and explicit UTC timestamps. All before readings must precede
interventions, which must precede all after readings. Repairs and retrofits require
an intervention; refurbishment inspections may have none. `source_locator` identifies
the row/channel in the retained original export. Conditions describe input voltage,
load current, ambient temperature and settling time; matching declarations do not
prove the actual bench conditions matched.

Supported row units are V, mV, A, mA and uA. Checks, calibration ranges and budgets
use canonical V or A, with quantity `dc_voltage` or `dc_current`. Decimal values
must be finite strings. Correction and standard uncertainty use the row's unit.
Corrected value is `(value + correction) × unit_scale`. Use correction zero if it
was already applied to the exported value; do not apply it twice.

Supply a strictly positive **combined standard uncertainty**, accounting for the
actual setup, and its budget with scope `combined_standard_declared`. Expanded
uncertainty is `k × u`; k is declared per check and does not by itself establish
a 95% confidence interval. The tool compares the resulting closed interval with
inclusive lower/upper limits: inside, disjoint outside, or overlapping. A before/after
delta is arithmetic only; its uncertainty requires a covariance model and is not
calculated. Expired/out-of-range calibration and missing/late criteria approvals
remain explicit blockers. Structural inconsistencies refuse the import.

## Scope and qualification

This initial adapter covers scalar DC endpoints, not resistance, waveforms, ripple,
impedance, batteries, mains, insulation or automated instrument control. It does
not diagnose component faults, authenticate certificates, certify safety or authorize
return to service. Hardware/firmware changes are recorded for retrofit traceability;
compatibility is not inferred.

Physical qualification still requires actual calibrated measurements, independently
held-out references and agreed criteria. Before/after readings of the same device
are not that independent reference. Release remains unchanged.

The output retains the exact manifest, a report, the HTML view and evidence bytes
under `raw/<hash>.bin`. Preserve the original input directory for replay: the retained
manifest's relative filenames refer to that input layout, not the hashed archive.

## Validation

Run `python -m pytest -q tests/test_electronics_repair.py`. Browser validation is
in `validation/electronics_repair_browser.cjs`; it accepts the result directory,
an installed Playwright module path and a Chromium executable path.


## Spatial inspection view

The offline viewer includes a shaded 3D assembly illustration with orbit, zoom,
oblique/top/front camera presets, and an assembly-separation slider. Drag the scene,
use the visible buttons, or focus the scene and use arrow keys, +/-, and Home.
There is no automatic rotation or continuous animation. SVG projection runs locally
without WebGL, downloaded libraries, or network access.

Numbered scene markers link to measurement pairs and stay synchronized with the
condition/search filters, selected pair, readings and uncertainty plot. The phase
selector changes the scene readout only. Camera and separation controls never change
recorded measurements, evidence, comparison results or exported reports.

Geometry is illustrative for every case, including operator measurements. The case
schema supplies no CAD, dimensions, pinout or verified spatial mapping. Marker
positions are stable display slots, not physical probe locations. Generic components
are not matched to intervention part IDs. Large cases should use the filters to
reduce overlapping markers. Full physical-model inspection requires a separate
verified geometry and placement adapter.
