# CI inbox failure recovery — 2026-09-27

The initial failure snapshot was commit `033703ad7ee5164fe2d1164f5d2956ee7b8c4dea`.
Its missing packaged Workbench fragments and corrupt compressed payload broke
source imports and installed-wheel startup. The later main commit
`d241242a06f26550a43701f44323b2c1d7ad6981` repaired those transport failures,
but its decoded source was the preceding Workbench without project-graph
registration. The checked-in project/energy tests correctly detected the loss.

## Repair

- Replace the runtime fragment reader and compressed `exec` payload with ordinary
  readable `workbench.py`. Its pre-edit source is exactly historical Git blob
  `d136f0a31be80a324db16ea591cfc3562f12f61d`. Only the six explicit project-graph
  registration edits are applied to that decoded source. Remove the obsolete
  fragments and their package-data entries.
- Restore project source dispatch, operation registration, builtin availability,
  instrument role, and declared/reproduced lifecycle participation. Preserve
  native owner algorithms, replay identities, evidence and proof boundaries.
- Group automatic CI by workflow, event and branch/PR, not a unique push run ID.
  Explicit manual runs retain separate groups. Matrix failures no longer cancel
  other platform outcomes. No test, workflow trigger or proof gate is removed.
- Wire selected-repository credentials for the private-provider jobs. The
  standard workflow token cannot read the separate private repositories.
  Missing credentials remain a failed/unqualified gate, not a passing skip.

## Operator credential boundary

Set the repository Actions secret `CIW_PROVIDER_READ_TOKEN` to a **read-only,
selected-repository** credential for the private provider repositories used by
these integration gates. Prefer a controlled GitHub App installation-token
provisioning process; a fine-grained token must be limited to Contents: read and
only the listed providers. Do not use an unrestricted classic personal token.
The helper's `PROVIDERS` set lists the permitted repository destinations; it does
not grant or attest the credential's actual permissions.

No credential was created, retrieved or granted by this repair. The secret is
not passed to pull-request jobs or ordinary feature-branch pushes. Main/tag
pushes and operator-dispatched jobs can use it. Public pull requests therefore
cannot qualify private providers through this credential path.

`scripts/ci_provider_access.py` configures only process-scoped Git transport.
Tokens are not written to Git configuration, URLs, environment files or artifacts.
The helper refuses other hosts, owners, repositories and ambiguous paths, and
never stores credentials. Explicit secondary `actions/checkout` steps also use
the selected-repository token instead of the default single-repository token.
The existing provider commits and numerical/proof checks remain unchanged.

Actual private checkout and scientific execution must still pass after this
secret is provisioned. The SP1 gate additionally retains its existing RAM/disk,
toolchain, guest-identity, genuine-proof and negative-test requirements. Native
unit-test success is not cryptographic qualification.

## Trusted-run access and installation repair — 2026-10-04

The push/main runs at `68084b67be8d8345351bcdd6e3ce62aafb8a868d` failed
before provider tests because the credential was empty, including
[telemetry](https://github.com/atomtrapping/Notations-Engineering-Terminal/actions/runs/37220769523)
and [acquired stream](https://github.com/atomtrapping/Notations-Engineering-Terminal/actions/runs/37220769591).
Their event expressions already permit the secret on trusted main pushes.
This is distinct from intentional withholding on pull requests; logs establish
that the secret was unavailable to those jobs, not whether it was absent or
restricted in account settings. The helper now checks event policy
first and gives different diagnostics for untrusted events, missing credentials
and malformed values. Every refusal still fails qualification. Its Git helper
uses the configuring Python interpreter, avoiding a later bare `python` lookup
that can resolve to an unavailable Windows application alias.

A repository administrator must configure `CIW_PROVIDER_READ_TOKEN` under
**Settings → Secrets and variables → Actions** for
`atomtrapping/Notations-Engineering-Terminal`, using a read-only credential for
the selected provider repositories. If supplied by an organization secret,
allow this consuming repository; an environment-only secret is not available
to jobs that declare no environment. Verify the credential's provider repository
selection, expiry and any required approval/authorization. Keep the value out
of logs, artifacts and issue text. Then dispatch the affected unchanged gate
on the selected immutable candidate and inspect its actual checkout and
scientific results. Configuration success is not qualification.

The spatial provider is now canonically
`atomtrapping/Notations-FrameMapper-RunTime` (repository ID `1347707016`). The
existing execution pin `acc6a4c43dd1d28e3fc6890dadf5cd2b4ae40125` was verified
at tree `6fd52b3a1bd4f27ec73eedc814368a94bc4695fc`, with its original
`tools/ciw-local-frame` worker and tests. That repository is currently private;
the workflow uses the same trusted-event credential policy and exact pin.
A public PR receives no private access and cannot qualify that provider.
The published FrameMapper import has a different revision and does not replace
this missing execution history. No private source is imported by this repair.

The established [public provider gates](../scripts/check_public_provider_gate.py)
can use already retained history for their four declared compositions; the
[alternative-source guide](CI_ALTERNATIVE_SOURCES.md) describes provider-owned
and local pinned-checkout routes. Migrating another gate requires its complete
exact provider set and unchanged checks; an arbitrary public branch or current
import cannot substitute for a required private pin.

Irrigation CI previously created its installed environment inside the source
checkout while the installed gate expressly refused that location. The workflow
now creates that environment under `RUNNER_TEMP`, preserving the outside-source
check, and uploads receipts even after a failed gate. Passing source tests alone
does not close the installed qualification.

## Reproduction

```sh
python -m pip install -e '.[dev,openusd]'
python -m pytest -q tests/test_ci_recovery.py tests/test_project_workflow.py tests/test_energy_workflow.py
python scripts/check_ci_policy.py
python -m pytest -q
python -m pip wheel --no-deps . --wheel-dir dist
```

The focused source regressions initially changed from five project/energy
failures to 27 passes. The credential/source/CI-policy additions expand that
focused suite to 49 tests; all 49 passed locally (Linux, Python 3.13.5,
NumPy 2.3.5). Real Git credential-protocol tests use an explicitly synthetic
secret without making remote requests. They do not establish private access.
The pinned CI run separately retains its environment, JUnit reports, installed
wheel tests and real OpenUSD roundtrip. Count skips separately.

Historical failed notifications are evidence of older runs and are not erased
or relabelled. Qualification applies to an exact new commit and its runs.
