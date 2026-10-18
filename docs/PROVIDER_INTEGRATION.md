# Provider discovery and explicit native console bindings

The installed NCP catalog, MCP control host and local console now share source
navigation for all **21 imported modules, 26 surrounding systems and 19 external
tool boundaries**. Entries project the existing manifest/catalog and the actual
workflow provisioning declarations. They do not probe upstream repositories,
load a provider, restore a saved binding or assert qualification.

```sh
ncp catalog --integrations imports --json
ncp catalog --integrations imports --source state-inference --json
ncp catalog --integrations systems --json
ncp catalog --integrations external --json
```

On the existing control MCP profile, use `ncp_integrations_list` with `section`,
`offset` and `limit`, or `ncp_integration_describe` with `section` and an exact
`source_id`. Both are read-only tools, also available in the local console.
`ncp_capabilities_list` remains the authority for the current host's bound and
granted executable operations. Source discovery never broadens that set.

Nineteen imported roles have existing Workbench workflow connections; the two
npm modules retain their separate client/build boundaries and null Python
runtime pins. Each connection retains its exact runtime revision and external
prerequisites. For example, GSIE calibrated-window and calibrated-observable
use different revisions; SET also has separate current and historical profiles.
The listing preserves all these declarations rather than selecting one global
checkout. A declared path is distinct from an executed or qualified binding.

## Native BIM clearance through the same control interface

The existing `metrology.bim-clearance.v1` calculation can now be launched through
`ncp control serve` and the local console, using the same pinned provider,
AgentHost, typed plan, admission, durable job and retained result owners. Native
execution is available only when the operator supplies its explicit checkout
and full revision. The registered CSE tree remains
`f91c982b580ad4f16a27ea149eedf0e338f6d764`.

For an exact local checkout, preserve the original CSE import from NCP history:

```sh
git -c core.autocrlf=false clone --shared --no-checkout /absolute/NCP /absolute/cse-clearance
git -C /absolute/cse-clearance -c core.autocrlf=false checkout --detach f1ceeae28168de32ca9aea44f7b6d67d2090677e
mkdir -p results
ncp control prepare-bim --inspection examples/metrology-clearance/uncertain-inspection.json --output-dir results/bim-profile
ncp control serve --profile /absolute/NCP/results/bim-profile/profile.json --instrument bim-clearance --provider-checkout /absolute/cse-clearance --provider-revision f1ceeae28168de32ca9aea44f7b6d67d2090677e
```

Install the existing `mcp-control` extra for the server; the console has its own
pinned requirements in `tools/mcp-operator-console/requirements.txt`. Native
dependencies belong to the explicitly selected interpreter. No command above
downloads provider source or runs a scientific operation. The local shared clone
depends on retaining its source object store; preserve that source checkout.

Launch the console using its existing private token-file setup and append:

```sh
--instrument bim-clearance --provider-checkout /absolute/cse-clearance --provider-revision f1ceeae28168de32ca9aea44f7b6d67d2090677e
```

An optional `--provider-python /absolute/python` selects the native interpreter
in the console; the server launch equivalent is `--python`. These are trusted
process-launch options. Browser and MCP tool arguments cannot supply paths,
change provider revisions or add grants. The console also supports the existing
measured-thermal profile and the fixed-pin leakage profile; leakage rejects a
revision override. Operational investigation stacks stay in their original frozen
profile and cannot be combined with these instrument launch flags.

After discovery, create a plan from the frozen baseline, admit its exact digest,
submit, and inspect the returned run. A retry of the same attempt reuses the
retained run; a new attempt creates a new occurrence. Resume never redispatches
an ambiguous interrupted claim. Clearance's fixed graph, frame, pose, calibration
and uncertainty checks are unchanged. A completed job may retain HELD or
UNRESOLVED scientific outcomes; no physical acceptance is inferred.

CSE quantity conditioning still uses its separate original runtime pin
`4b74abda40bba3277de69bf61e9e09283ae2d5b3`. The clearance source selection does
not repin that profile. Original provider licences and notices remain attached.

## Source consistency and evidence

`scripts/update_integration_sources.py --check` compares installed discovery
data with `instruments/manifest.json`, `docs/SYSTEMS_CATALOG.md` and all nine
runtime declaration files. The existing control and consolidated workflows run
this check. When those source declarations change, regenerate the packaged
projection with `python scripts/update_integration_sources.py` and review it.
Saved investigations cannot replace this installed trusted metadata.

The existing installed SDK gate now exercises BIM clearance with both modern
and legacy clients. The browser gate accepts an explicit BIM inspection and
native binding, and checks browser → HTTP console → MCP → retained native result.
Source tests cover unchanged grants, separate runtime pins, invalid launch
combinations, unadmitted execution refusal, retry, fresh occurrences and resume.
Executed results and open qualification gates belong in the dated integration
evidence ledger; configured CI alone is not evidence that a run passed.

The [latent-surface audit](LATENT_PROVIDER_SURFACE_20261018.md),
[provider availability guide](PROVIDER_AVAILABILITY.md) and
[integration coverage](INTEGRATION_COVERAGE.md) retain the other boundaries.
OpenTelemetry/OpenObserve and the candidate external solvers remain planning
or optional profiles as declared; this interface change does not implement them.

Executed results: [18 October 2026 integration audit](PROVIDER_INTEGRATION_20261018.md).
