# Graph-first Industrial Lab

The Lab projects an existing scientific-system specification into NET's existing
System Board. It runs the same compile, simulate and verify operations in one
ordinary Session. No 3D engine, new physics engine, graph database, CAD converter
or general visual-programming compiler is introduced.

This first profile is the existing thermal rod, quasi-static axial mechanical
response and synthetic rod observer. It is not a MEMS board, injection-molding
or blow-molding simulation. See [scientific composition](SYSTEM_COMPOSITION.md)
for the equations, coupling limits, numerical checks and physical-validation gap.

## Run

From a checkout with the normal NET dependencies installed:

```sh
net system lab demo --output-dir results/lab-input
net system lab serve results/lab-input/specification.json --output-dir results/lab-session --allow-run
```

Open the exact loopback URL printed by `serve`. The existing Board server issues
an ephemeral token in the URL fragment. Treat that URL as a session credential.
Do not publish it or expose the server externally. The application uses the
existing fixed endpoints, Host/Origin checks, request token and content-security
policy. No provider path or arbitrary command comes from the browser.

Select a model, run the baseline, edit an exposed numeric parameter and choose
**Compile candidate**, then **Run complete candidate**. Preview compiles the
native specification but does not execute a provider. Each candidate targets the
original baseline and changes one parameter. A candidate is not promoted into a
new baseline. Use a new specification and Lab session for a cumulative design.

Without `--allow-run`, the server permits inspection and retained proposals only.
`--engine subprocess` is the default; `--engine local` selects the existing local
reference. OCI remains available through native system workflows, not this
bounded interactive profile. No physical control is exposed.

## Offline graph and headless execution

The demo includes `board.html`. Open it locally to inspect nodes, arrange the
canvas and export a typed parameter request as `board-edit.json`. No computation
runs in this HTML file. The exported request binds the exact baseline Board.

```sh
net system lab view results/lab-input/specification.json --output results/lab-offline.html
net system lab run results/lab-input/specification.json --edit board-edit.json --output-dir results/lab-run
net system inspect results/lab-run/session/workspace.json
```

Omit `--edit` for a baseline-only run. Headless `run` is explicit execution consent;
it retains a native workspace, a numerical `review.html` and `summary.json`.
`run` returns 0 when the executed native numerical reports pass, and 2 for a
numerical failure or refusal; inspect the structured status to distinguish them.
Output directories/files must be new. Inspection and workspace restoration do
not silently rerun physics. Explicit native replay creates new execution/result
occurrences, not fresh physical measurements.

## Graph and evidence contract

The authoritative input remains `ciw.system-spec.v1`. MODEL nodes and
SEMANTIC edges are derived using the existing `ciw.system-board.v1` contract.
The projection preserves source node/coupling identities and explicit native
coupling metadata. No empty visual socket is presented as a physical connector.
The native system compiler, not the ordinary Board OPERATION compiler, decides
whether this profile can run.

Canvas coordinates, zoom and selection never modify geometry or physical
placement. Shared geometry, units, clocks, boundary kind, model identities and
topology are not edited by this UI. Authoring ranges are numerical input budgets,
not calibrated limits or assertions about a material. Original source formatting
and bytes remain in Workbench; normalized candidate bytes are retained as a new
source. Sources are saved before the first simulation.

Each baseline/candidate creates separate `system.compile.v1`,
`system.simulate.v1` and `system.verify.v1` operation results. The native verifier
payload retains its own verification occurrence; generic result records keep
their existing identity/status semantics. The Board view does not mint a verifier.

Plots copy retained trajectories. Dashed baseline and solid candidate lines join
native samples for presentation only. The full numerical arrays and scoped
verification record remain inspectable. These are scenario overlays, not
`system.compare.v1` mesh-reduction checks, causal validation, calibration or
physical measurements. The complete coupled model is rerun; dependency highlighting
does not claim Needle reuse or partial scientific recomputation.

## Bounds and unchanged interfaces

Input specification: 64 KiB. Interactive model: at most 64 cells and 400 timesteps.
The inherited Board transport limits requests to 64 KiB and a work session to
256 retained actions. These input bounds do not guarantee wall-clock deadlines,
process sandboxing or enforced memory limits; native runtime receipts retain those
separate declarations. Unsupported units, models, couplings, stale references,
nonfinite inputs, undeclared edits and representation promotion refuse explicitly.

The ordinary oscillator Board still uses its existing runner and Needle paths.
The Lab changes neither Session nor Workbench nor provider or operation registries.
No new HTTP server or endpoint is introduced. Browser refresh inspects the latest
baseline/candidate in the current process without rerunning them. Restoring a
workspace for numerical inspection is supported; resuming a stopped interactive
Lab process with all action/UI state is not implemented.

CAD/BIM imports, KiCad/MEMS/Link integration, free graph topology authoring,
vibration models, real acquisition, physical machine placement, simulation-to-
measurement residuals, parameter-sweep scheduling, Godot views and 3D are separate
extensions. Reuse their existing owners and native contracts, including the
retained-data and sensor-project branches. Do not relabel this synthetic reference
as a PCB, a calibrated instrument or a complete industrial twin.

## Verify

```sh
python -m pytest -q tests/test_industrial_lab.py
python scripts/check_industrial_lab_browser.py --output-dir results/lab-browser
python scripts/check_industrial_lab_browser.py --offline --output-dir results/lab-offline-browser
```

The browser script needs Playwright and its Chromium, or an explicit `--browser`
executable. The live campaign tests the actual Board HTTP transport, native
baseline/candidate execution, model selection, plots and refresh. The offline
campaign uses a real DOM without URL navigation, exports an actual edit, and runs
it through native subprocess execution. The offline campaign is not a substitute
for live-browser network qualification. Neither supplies physical evidence.

The dedicated CI workflow repeats source and noneditable installed-wheel tests
on Linux/Windows and separately attempts the live browser campaign. An unavailable
or blocked environment is not a pass. Existing qualification workflows are not
narrowed or waived by this increment.
