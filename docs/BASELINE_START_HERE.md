# Start with one retained investigation

Selected code: `312a37ba613a0b57ea027b8d00df5b3b4fe08d69`. Install the matching
wheel from the evidence bundle. Use a new output directory for each run.

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
