# Start with one retained investigation

Selected code: `312a37ba613a0b57ea027b8d00df5b3b4fe08d69`. Install the matching
wheel from the evidence bundle. Use a new output directory for each run.
The matching wheel SHA-256 is
`40c6b0410fd6f28f03023cf292553c10fd293b767d66743310616b8b6306ca9e`.
The later [provider integration](PROVIDER_INTEGRATION_20261018.md) has its own
code and wheel identities even though the package filename/version is the same.

On Linux or macOS:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install /absolute/path/computational_instrumentation_workbench-0.1.0-py3-none-any.whl
.venv/bin/ncp doctor --profile core
.venv/bin/ncp start --output-dir results/first-use-001
```

On Windows, use `py -3.12 -m venv .venv`, then the equivalent
`.venv\Scripts\python.exe` and `.venv\Scripts\ncp.exe` commands. Activation and
administrator access are unnecessary.

Open `results/first-use-001/readiness.json`. It retains the preflight, synthetic
oscillator, thermal and atmosphere outcomes and their exact record identities.
Retained FAIL or INDETERMINATE scientific comparisons remain visible; a completed
first-use receipt does not establish physical validity or provider qualification.

For the persistent local console, continue with [the quickstart](quickstart.md).
For assistant-facing tools, choose an explicitly authorized profile from
[operational investigations](OPERATIONAL_ENGINEERING.md),
[numerical operations](NUMERICAL_BUILTINS.md) or
[bounded BIM clearance](METROLOGY_CLEARANCE.md). The console and MCP call the
same underlying operations and retained evidence.

Developers can use [the cloud setup](CLOUD_DEVELOPMENT.md). Reviewers should use
[the selected baseline ledger](RESEARCH_BASELINE_20261017.md) for exact test
results and remaining public-CI, private-provider, platform and physical gates.

Maintainers should use the [workflow lane review](WORKFLOW_LANE_REVIEW_20261018.md)
for per-module CI comparisons and the disposition of pending hosted jobs. A
repeated test filename alone does not identify a redundant qualification lane.

For teaching and retained visualization on current main, use the
[single host export/launch path](RETAINED_RENDER_CONTRACT.md): Godot displays
cards and status; Bevy displays paths and gizmos from the same retained JSON.
These post-baseline tools have separate evidence and do not qualify the frozen
SHA above. The bounded Bevy projectile runtime remains a separate registered
simulation profile.
