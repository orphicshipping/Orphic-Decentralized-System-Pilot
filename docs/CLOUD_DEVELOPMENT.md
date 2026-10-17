# Cloud development environment

Use the existing checkout in an isolated cloud task; do not create a Git worktree
unless explicitly requested. Run the commands from the checkout root. Python
3.11 or newer is required; the consolidated CI lane selects Python 3.12.

## Install

This follows the common dependency installation in
`.github/workflows/consolidated-integration.yml`. The `external-work` and
`legibility` extras supply MCP, JWT and cryptography dependencies that `dev`
alone does not include. External provider qualification is separate.

```sh
cd "$(git rev-parse --show-toplevel)"
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,external-work,legibility,particles,numerical-checks,bench-models]' build
.venv/bin/python -m pip install --no-deps \
  -e instruments/measurement/signal-processing \
  -e instruments/measurement/signal-conditioning
.venv/bin/python -m pip check
.venv/bin/net doctor --profile core
source .venv/bin/activate
```

Keep source, tests, dependency declarations and lockfiles unchanged during
environment setup. Virtual environments and generated results are ignored.

## Run and retain a local investigation

Choose a new output directory for each first-use run:

```sh
net start --output-dir results/first-use-cloud-001
```

Inspect its `readiness.json` for bounded synthetic oscillator, thermal and
atmosphere checks. For a persistent workbench, run:

```sh
net workbench --resume --output-dir results/cloud-workbench --port 8765
```

In a second terminal, activate the same environment and run:

```sh
ciw health
ciw send analysis.stats
ciw send result.list
ciw send workspace.save
```

`analysis.stats` creates a result. Save before stopping the server. Restart the
same workbench command and inspect `result.list` to confirm retained results.
The service binds to loopback. Processes must be restarted in a restored cloud
environment; retained files and installed dependencies are separate from live
processes. This workbench is not a hosted System Board HTTP backend.

## Local Board MCP

Create a new synthetic demonstration profile, then inspect or serve it:

```sh
net refit demo --output-dir results/cloud-board-refit
net refit status --profile results/cloud-board-refit/profile.json
net refit serve --profile results/cloud-board-refit/profile.json
```

The generated `mcp-config.json` supports a stdio client. Only one process may
write a profile at a time; stop its server before opening it through another
launcher. Creating a profile does not execute its baseline or candidate. Follow
[Board MCP refit](BOARD_MCP_REFIT.md) for grants, execution and review boundaries.

## Validate

From the activated environment at the checkout root:

```sh
python -m pytest -q \
  tests/test_board_refit.py tests/test_board_refit_cli.py \
  tests/test_board_refit_mcp.py tests/test_board_refit_review.py \
  tests/test_link_refit.py tests/test_link_refit_cli.py \
  tests/test_visual_board.py tests/test_needle.py tests/test_system_board.py \
  tests/test_parameter_program.py tests/test_semantic_capabilities.py \
  tests/test_operator_catalog.py
python -m build --wheel --outdir results/onboarding-artifacts/wheels
```

Onboarding at `cc5d7f31cfeaeb4d897e0864e0fcc560355a0d39` on 2026-10-17
verified installation, dependency consistency, the wheel build, first-use
synthetic workflows, and workbench calculation/save/restart. The selected suite
completed with **162 passed, zero failures and zero skips**, including local
stdio MCP journeys. This is historical evidence for that checkout and environment,
not a full-suite or release qualification claim.

Native providers, Windows, SP1, hardware and private datasets require their own
documented setup and qualification. Hosted HTTP integration, remote MCP and
browser interaction were not validated by this onboarding. Reuse existing
platform Git authentication and test the required read operation before
requesting credentials; never save secret values in these instructions.

For the consolidation close-out, run `python scripts/monorepo.py` and
`python scripts/check_consolidated.py --output-dir results/consolidated-check`.
These require retained native Git ancestry and original source objects, not a
file-only export. Keep every per-module refusal, skip, timeout and JUnit report.
The selected candidate and current results are recorded in
[the baseline ledger](RESEARCH_BASELINE_20261017.md); the onboarding counts above
remain historical. Installed-wheel acceptance must run outside the source
checkout with isolated imports.
