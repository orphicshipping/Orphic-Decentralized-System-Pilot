# Provider integration evidence — 2026-10-18

Selected code `0e4aeafd44fcf3ae3e25808550903b32260eeaa1`; tree `e4a277693b63dde7d53cb319efdc490edde58c0a`. PR #245 merge `6c7c46231757b7ce79f95e07c35840268140a4e5` has the same tree.
The dated [audit ledger](../../docs/PROVIDER_INTEGRATION_20261018.md) explains
the implemented connections, exact wheel and qualification limits.

- `implementation.json`: candidate attribution and actual executed results.
- `integration-connections.json`: all 21 imports, 26 systems, 19 external boundaries and original per-workflow pins.
- `provisioning-preflight.json`: final 29 workflow plans, 15 ready / 14 blocked; no execution authority.
- `regression-summary.json`, `regression-counts.json`, `skip-reasons.json`: 516 modules and all explicit skips.
- `full-regression.tar.xz`: original per-module XML, JSON and log files.
- `installed-native-console.tar.xz`: installed/native/SDK/browser/console reports, transcripts, synthetic retained results and supplemental harnesses.
- `earlier-attempts.tar.xz` and `earlier-attempts.json`: superseded drafts/harness failures with fixes; excluded from qualifying results.
- `archive-manifest.json` and `SHA256SUMS`: archive/member and file integrity.
- `hosted-ci-observation.json`: complete status metadata, not a qualified CI artifact.

All counts are overlapping gates. Full regression/wheel results apply to the
selected code. The evidence follow-up also carries a console fixture path
portability correction, verified by rerunning the seven console tests only.
No Windows, physical equipment, external actuation or deployment was qualified.
The exact wheel is included in the downloadable evidence ZIP, not duplicated
inside this Git evidence directory.

To reproduce from a complete exact-history checkout, use CPython 3.12 and the
recorded toolchain, install the selected wheel noneditably, and unset ambient
`PYTHONPATH`. Confirm `python -I -c 'import ciw; print(ciw.__file__)'` resolves
to site-packages. Source-wide regression uses
`python -I scripts/check_consolidated.py --output-dir /absolute/new-results --workers 4`.
It requires the original retained history objects; a shallow or source-only
snapshot is not equivalent. Run
`python scripts/update_integration_sources.py --check` for projection consistency.

For native paths, follow [PROVIDER_INTEGRATION.md](../../docs/PROVIDER_INTEGRATION.md),
retaining the exact provider sources and interpreter dependencies. The real
control gate is `scripts/check_control_mcp.py`; browser journeys are under
`tools/mcp-operator-console/tests/browser_journey.py`. Both accept explicit native
BIM arguments. Supplemental harnesses retain their original local paths as a
record of the executed campaign; adapt ROOT and fresh output locations before
reproduction. Synthetic source inputs and retained transcripts are in the raw
archive. Private/platform/physical and human-acceptance gates remain independent.
