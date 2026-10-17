# Measurement evidence visualizer

The measurement view turns the retained public evidence v2 package into a
standalone interactive HTML file. It uses the workbench's existing visual
language and offline rendering pattern. Opening or filtering the view does not
run an instrument, modify measurements or change qualification status.

## Open the evidence

With the development package installed, create a new HTML file:

```text
python -m ciw.measurement_visualizer --evidence-dir public-measurement-evidence-v2 --output measurement-visualizer.html
```

Open that file in a browser. It includes all necessary data, CSS and JavaScript;
there are no external fonts, libraries or automatic network requests. The
source-report links open the corresponding primary publications when selected.
No source PDFs need to be downloaded to use this view.

An existing output filename is refused. The input bundle is read only. The
renderer checks selected files against `SHA256SUMS`, compares retained campaign
copies and report identities, and joins rows by exact record IDs. These checks
establish internal file consistency, not publisher authentication or a fresh
scientific verification. Reproduction of source calculations remains the job
of the [published-data adapters](PUBLISHED_THERMAL_AND_DSC_EVIDENCE.md).

## Review a measurement

Choose an instrument family in the left rail, then select a plotted point or
table record. The inspector shows the value, uncertainty scope, reference,
source issues, source-page link, source digest and original decimal strings.
Point and table selection stay linked. Plot points also support keyboard
selection with Enter or Space.

Search by laboratory, specimen or material. Comparison views can show only
source-review rows. DSC filters select a sample, ingot or nominal heating rate.
Reset restores the full dataset. Empty selections clear the inspector rather
than leaving a previously selected record visible.

Export selected or visible records as JSON. Each export retains the source
identity, exact raw records, view filters and unchanged qualification status.
It is a view selection, not a new experimental result or an acceptance record.

| Evidence | Default visualization | Scientific meaning |
| --- | --- | --- |
| Thermal: six external results | Signed difference with reconstructed expanded intervals, in microkelvin | Conditional source-component calculation; two coverage ambiguities and one conflicting uncertainty budget remain flagged |
| Chemistry: 24 results | Signed mass-fraction difference and expanded interval, in mg/g; filter by level | Descriptive k=2 calculation with an explicit zero-covariance assumption |
| Density: six results | Signed density difference and expanded interval, in kg/m3; filter by liquid | Only this dataset displays the source's strict En<1 decision; two source-review rows remain visible |
| DSC: 180 measurements | Measured heating rate versus enthalpy; color identifies ingot | Individual observations from 15 samples, seven ingots and one lot; no fitted line or certificate target |

DSC uncertainty bars are off initially. The optional bars show only plus/minus
the tabulated **systematic standard uncertainty**. They do not represent total
per-measurement uncertainty or uncertainty of the mean. Replicates are not
connected as a time series, and table order is not labelled acquisition order.
The calibration material is indium; no polymer endpoint is qualified by this
view. Mechanical and polymer specimen-data gaps remain visible in the family
rail.

The two thermal pilot controls are retained in the method details and excluded
from the six external plotted candidates. Source-review flags have text labels
as well as color. A descriptive interval containing zero is never relabelled a
qualification pass.

## Relation to the System Board

This is a read-only sibling of the [System Board view](VISUAL_SYSTEM_BOARD.md).
It does not attach public measurements to a Board execution or fabricate
`evidence_projection` provenance. Existing Board compilation, Session/Needle
execution and representation gates remain authoritative for those workflows.
The renderer reuses inert base64 payloads, packaged assets and a hash-based
Content Security Policy. Source text is inserted using text nodes; there are no
general file-opening, shell or provider-control endpoints.

## Verification

`tests/test_measurement_visualizer.py` exercises provenance, inventory and
schema refusal, row mapping, scientific-status preservation, inert rendering
and create-only output. `validation/measurement_visualizer_browser.py` exercises
the actual browser controls and retains screenshots and a report. Browser
validation checks linked selection, filters, uncertainty visibility, exports,
empty states, hostile source text, and narrow layout.

The public release and local physical qualification status remain unchanged.
