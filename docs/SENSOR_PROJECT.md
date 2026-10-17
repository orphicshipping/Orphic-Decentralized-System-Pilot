# Retained electronics, MEMS and visual-asset workflow

This additive integration uses existing NET file inventories, ObservationBus,
CapabilityRegistry, OperationRegistry, Session retention, and MCP transport.
It does not replace the Workbench, Link, manufacturing reducers, glTF consumers,
or the engineering tools with a new digital-twin engine.

## Responsibility and delivered boundary

| Tool | This increment | Not established by it |
| --- | --- | --- |
| KiCad 10 | Explicitly bound CLI adapter for ERC, DRC and BOM/position/Gerber/drill/STEP/GLB exports; exact original design and rule/library files retained | Native execution on this host, dependency/library closure, actual sensor-board correctness, fabrication approval |
| MEMS Studio | Exported CSV ingestion with explicit column/sensor freshness masks, exact configuration and row/byte ancestry | GUI automation, raw Datalog binary decoding, acquisition completeness, clock or calibration qualification |
| Blender | Original `.blend` retention and a fixed, hash-bound candidate metadata annotation recipe | Native Blender execution, STEP import, automatic geometry equivalence, full asset authoring or glTF roundtrip qualification |
| Godot | Data-only view export and a read-only component resolving existing scene nodes to NET evidence | Native Godot execution of this component, live Link streaming, interactive machine control or a validated physics model |
| Link | Existing `ciw.observation-stream.v1` exports consumed unchanged; original capture/workspace/covariance may accompany them as retained artifacts | New Link app, driver, fabricated device identity, new physical acquisition |
| NET | Retained composition, explicit modes, offline inspection/MCP, new-occurrence replay and candidate output export | New canonical state owner, warehouse/data-family bridge, certification or release authority |

A passing rule check is not manufacturing approval. A render is not engineering
truth. A successful numerical operation is not empirical validation. All example
sensor values and native-file placeholders used by the tests are synthetic.

## Existing integration dependencies

Base: NET main `0374076695475669188d61698e4656ced2263e01`, Python package tree
`0b462bbb9ab1f6beddc3e187a1999e499688cd11`.
The existing Blender workbench and interactive simulation recipes remain intact.
PR #201's retained glTF/native Godot consumers remain separate; no GLB mesh parser
is duplicated here. Parent #200/#185 owns the shared data and Workbench bridges.
Existing Link #204/#205/#209, device review, metrology, native solvers, private
GaugeLink design files and the operational-observability plane are untouched.

## Retain a project through Session

Create a directory containing only the reviewed input files, plus a request JSON
outside that directory. The request uses `ciw.sensor-project-request.v1` and lists
every retained file, its role, caller-declared authoring tool/version and source
parents. Native project/schematic/PCB, local symbols/footprints/rules, mechanical
CAD/STEP, Blender/GLB, settings, original logs, native observations, calibration,
firmware and manufacturing records can all be retained without conversion.
No native format is declared valid just because a filename has the right suffix.

```sh
python -m ciw.sensor_project_cli run INPUTS request.json --output-dir project-new
python -m ciw.sensor_project_cli inspect project-new
python -m ciw.sensor_project_cli replay project-new --output-dir replay-new
python -m ciw.sensor_project_cli export-inputs project-new --output-dir recovered
python -m ciw.sensor_project_cli export-view project-new --output view.json
python -m ciw.sensor_project_cli mcp project-new
```

One Session source declaration retains the original file inventory, exact input
bytes and exact request bytes. Its one-sample declaration is explicitly NOT an
acquisition clock or a fabricated measurement. The existing operation
registration mechanism executes `engineering.sensor-project.v1`; results, source
identity and execution occurrences stay distinct. A source revision produces a
new inventory/source; explicit replay preserves the old source and creates new
execution/result occurrences. There is no implicit recomputation on reopening.

`--expected-snapshot-ref sha256:...` may bind inspection, replay or export to the
exact workspace bytes supplied through a separate trusted channel. This is a
content check, not a digital signature or an origin-authentication claim.
Every output path must be new. No source file or existing output is overwritten.

### Bounds and local nature

The initial profile retains up to 128 declared files and 2 MiB total source bytes,
using the existing foundry inventory/file limits; workspace reads are capped at
32 MiB. Larger CAD assemblies require the parent's external artifact-store path,
not silent stripping, lossy compression, or a second store implemented here.
Secret-bearing, linked, hardlinked and nonportable inputs are rejected through
existing file-packet rules. These checks are not an OS sandbox against a hostile
concurrent operator or an untrusted native binary. Retained `.blend`/CAD files
remain inert until an operator explicitly opens a native tool.

## MEMS logging is not one acquisition per CSV row

Two deliberately distinct profiles are supported:

- `datalog2-column-mask`: LSB is the first sensor-data column, then successive
  columns. The full data-column order is explicitly declared, including ignored
  columns; this is not inferred from a filename or guessed sensor order.
- `explicit-sensor-mask`: the caller supplies zero-based bit indexes for each
  selected channel. Vector axes can share one sensor bit. Unknown bits refuse.

CSV header, delimiter, time unit/basis, export version, source origin, config hash,
selected quantity/unit/frame/model/entity and ignored columns must be explicit.
A set bit creates a new sample EVEN WHEN its numeric value equals the previous
value. A clear bit retains the original repeated export field but does NOT create
another measurement. A fresh empty field is withheld, not replaced with zero.
All source rows and byte spans remain referenced. No binary Datalog byte stream
is falsely claimed from a converted CSV. Optional native binaries can accompany
the export as opaque acquisition artifacts, without an invented decoder.

Time is converted from explicitly declared s/ms/us using decimal/rational parsing.
Reversal requires an explicitly declared segment start; segments have different
clock IDs. No host receipt time is substituted. Same-channel duplicate fresh times
refuse; unrelated channels need not be treated as synchronous. Clock relationships
and measurement uncertainty remain unknown. Units are retained labels; e.g. a
sensor acceleration label `mg` must not be relabelled as mass or converted to SI
without a separately declared, qualified transformation.

The source cap is 4096 CSV rows and 32 selected scalar channels. Unexpected masks,
headers, formulas, nonfinite values, lost-nonzero numeric underflow, oversize data,
and multiline records refuse. The caller's acquired/synthetic declaration is not
authentication; independent instrument validation remains necessary.

ST documents that mixed-rate logs repeat old values, and distinguishes the
sensor freshness field from the Datalog2 column-oriented `data_changed` field [2].

## Connect geometry to evidence without replacing either

A binding uses the exact visual asset hash AND explicit relative node path. It
names exact model/entity/quantity triples, native design parents, source/asset
frames, unit scale and a 4x4 affine transform. Selected native design paths must
occur in the visual artifact's declared ancestry. Other channels remain retained.
Missing channels remain visible. An explicit ancestry graph and transform are
operator declarations, not proof of geometry equivalence or as-built identity.
Unit serials, component lots, firmware, tests and calibration remain the existing
manufacturing records; this adapter does not create or release manufactured units.

The shared selector returns source references, original observation objects,
clocks/units/uncertainty, mode and unavailable qualification. It does not compute
an FFT or fabricate calibration/health/model outputs absent from the source.

REPLAY, SIMULATION and COUNTERFACTUAL are distinct. SIMULATION/COUNTERFACTUAL may
not relabel observed/estimated samples as simulated values. Counterfactual mode
also requires an explicitly retained baseline artifact; this is not proof of a
causal intervention or validation of an alternative process. This retained-file
path refuses LIVE. A separately qualified live receiver and freshness policy are
needed before a scene can legitimately display a live operating machine.

## Optional KiCad CLI execution

```sh
python -m ciw.sensor_project_cli plan-kicad INPUTS request.json
python -m ciw.sensor_project_cli kicad INPUTS request.json --output-dir checks-new \
  --executable /absolute/path/kicad-cli --sha256 sha256:EXPECTED_BINARY_HASH \
  --version 10.0.6
python -m ciw.sensor_project_cli export-kicad checks-new --output-dir fabrication-candidate
```

The native operation `electronics.kicad-package.v1` is separately advertised and
bound to the supplied executable/hash/version through the existing registry. A
saved project cannot pick an executable or supply shell commands. Only stable
10.0 patch-version labels are accepted; no nightly fallback or legacy SWIG use.
A fixed plan invokes native ERC/DRC with all severities, PCB/schematic parity,
then BOM, position, Gerber, drill, STEP and GLB. Code 5 from checks is a retained
violation outcome, not a process-crash substitute or fabrication approval.
Original project/rule bytes, commands, logs, version transcript and output bytes
are retained. Export reconstruction is offline and content-bound. Outputs always
remain a candidate, including after a clean native check.

The adapter reuses NET's bounded process supervisor and checks executable/source
copy drift between stages. It does not attest linked libraries, global symbol/
footprint/3D libraries, user preferences or remote resources. Review those against
the real deployment before treating outputs as reproducible manufacturing inputs.
For exceptional process/parse/output failures, Session retains the source and
refusal; full partial native scratch output retention is not implemented by this
first profile. Completed native capture is capped at 4 MiB total / 2 MiB per file.
Native replay requires explicit rebinding; ordinary `replay` refuses to substitute
another operation. No automatic fabrication order, board rewrite or flashing.

KiCad 10 has direct GLB export [1]. Use STEP for the mechanical CAD handoff and GLB
for Blender/Godot where appropriate; do not assume Blender natively imports STEP.
Custom MEMS/ASIC layout, semiconductor verification and foundry flows remain a
separate tooling boundary. This module does not implement the KiCad GUI/IPC API.

## Blender candidate and Godot view

The packaged `sensor_project_blender.py` is a fixed native-Blender recipe, with
stdlib-only parsing until the operator explicitly executes it. Run it using the
installed file path under Blender with `--background --factory-startup
--disable-autoexec --python-exit-code 2 --python RECIPE -- ...` and these arguments:

```text
--source reviewed.blend --source-ref sha256:EXPECTED_BLEND_HASH
--view view.json --view-ref sha256:EXPECTED_VIEW_HASH
--asset-ref sha256:BOUND_GLB_HASH
--output candidate.blend --report candidate-receipt.json
```

It loads the source with script execution disabled, resolves exact object paths,
adds representation-only NET identity metadata and writes a NEW candidate.
Missing objects are reported, not created or guessed. Original input files are
checked again after the operation. No geometry or engineering rule is changed.
This is not a complete automatic Blender pipeline or a qualified GLB roundtrip.
Native execution remains a gate; the test suite exercises its pure input checks,
not a synthetic bpy object falsely represented as Blender.

`examples/sensor-project/godot/net_sensor_evidence.gd` is a RefCounted helper for an
EXISTING Godot scene. The host supplies view hash, asset hash and scene root;
`load_view` resolves declared nodes without changing geometry or machine state.
`inspect_node` returns retained observations and evidence references. It is not a
new scene runtime, live network client, physics solver or machine controller.
The GDScript file is a source-level component, not a native-tested renderer here.
Godot's supported 3D import pipeline and NET's existing glTF consumer remain the
appropriate asset loaders [3]. Blender's documented glTF settings include custom
properties and Y-up export [4]; they still require exact project/version checks.

MCP exposes only `net_sensor_project` and `net_sensor_pick`, using NET's existing
server. Native binaries, filesystem paths, deployment and actuation cannot be
selected by tool arguments. Native result summaries omit binary payloads.
Operational OTel traces remain separate from all scientific and design identities.

## Qualification record and next physical gate

Synthetic fixtures contain deliberately invalid native CAD/GLB placeholders, not
an actual Notation board. Process doubles are named as such. Positive custody and
return-code tests are not actual KiCad ERC/DRC results. This scope was developed
without native KiCad, Blender, Godot, MEMS Studio or a connected sensor. No vendor
GUI/device compatibility, real design, warehouse union, physical or release
qualification is claimed.

The physical milestone still needs an actual pinned native sensor project and
its library/rule closure; installed version-bound native tools; a sensor capture
with configuration and source-clock/freshness declarations; geometry import and
frame/scale checks; and existing manufacturing record bindings. Passing this
module's software checks does not satisfy those gates.

Primary documentation reviewed 2026-10-06:
[1] https://docs.kicad.org/10.0/en/cli/cli.html
[2] https://www.st.com.cn/content/st_com/zh/technical-documents/UM3233.html
[3] https://docs.godotengine.org/en/stable/tutorials/assets_pipeline/importing_3d_scenes/available_formats.html
[4] https://docs.staging.blender.org/manual/en/latest/addons/scene_gltf2.html
[5] https://docs.blender.org/manual/en/4.2/advanced/command_line/arguments.html
