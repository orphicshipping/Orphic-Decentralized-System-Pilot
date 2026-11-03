# Native metrology qualification

This lane executes the existing calibrated-window and residual-monitor engines
through their real pinned Python sources and the principal-bound metrology
profile. It adds no scientific solver, synthetic provider, permission grant,
device connection or deployment. All input observations and calibration
declarations used below are explicitly synthetic fixtures.

The earlier missing-checkout limitation in [METROLOGY_OPERATIONS.md](METROLOGY_OPERATIONS.md)
is recoverable locally: all seven required commit objects are present in the
preserved ODSP Git object database. Isolated checkouts use those actual objects
at the unchanged runtime pins. This is reuse of imported source history, not a
claim that new upstream repositories were fetched or authenticated during this
qualification. Each checkout is verified by the existing
`PinnedSubprocessAdapter`, including HEAD, tree, tracked bytes, file modes,
untracked source files, interpreter digest and dependency versions.

## Exact native inputs

| Role | Commit | Tree |
| --- | --- | --- |
| TBRT | `40507060ca7a9126a9d641999b994a757eef3bfd` | `a797aaea6e7cb0aa6252333f3b14615bfed2840f` |
| MCUR | `49405ecd623474ddf601989b0a8195be401544e0` | `3bbd80f364dea3adc35dd762a1fc2847faddab73` |
| STFE | `54dd43b657b92d87c2fc263dfd99437c664be353` | `152816aca45290c81d770570851541edae69a706` |
| GSIE | `de38873db1ba98744d395dfa7df1d3b5b7bb3059` | `dc364802cf846329e48eaaf0832fa4d47c2abd35` |
| SET | `2f838f4e196f453efc3a59045b0b3ec4b5680296` | `54440032b98e24685cc500fd26abaa7ad7700a45` |
| FDIR | `29e4b306492b793487d47a96b97a56548217aa12` | `c7dc21c64377368341a2029ecd4f3e2a70d31646` |
| OIT | `db4c564bddbe1911f96585bd18f58659a0026fb7` | `3ba42834535b396a13395e5053c6fd5b4f1302c9` |

The GSIE and SET pins deliberately differ from some newer imported source
trees. The calibrated-window contract selects the versions above; substituting
the newer trees would change the runtime contract. MPL-2.0 and Apache-2.0 source
licences and notices remain in the individual checkouts; qualification grants
no additional redistribution or execution authority.

The seven tracked source trees total 672,875 bytes. Their normal runtime
dependencies require NumPy, already available locally. No compiler build,
package download or alternative numerical implementation was needed. This run
uses Python 3.12.14, NumPy 2.4.3 and SciPy 1.17.1, with pytest 9.0.2. The Python
executable SHA-256 is
`fa67443527ed9647f760d807e2a38f26340757123e643c4639cf273ed15d5ea7`.
Dependency versions are retained; this does not independently authenticate the
installed dependency binaries.

## Executed behavior

The existing `tests/test_calibrated_window.py` and
`tests/test_residual_monitor.py` completed with **63 passes, zero failures,
errors or skips**. They execute native estimation, replay, covariance and
observability checks; they also reject malformed and resealed false evidence.

`tests/test_metrology_native_completion.py` exercises the additional public
profile route: `services.invoke` → `PrincipalFacade` → `MetrologyHost` →
`OperationalHost` → `AgentHost` → existing native workflows. A fixed
operator-created job supplies the source, graph and runtime paths. Requests
select only the job, exact plan reference and attempt identity.
All **eight native integration tests passed with zero failures, errors or skips** in 117.38
seconds. The existing native suite completed in 204.61 seconds. During these
runs the metrology/native adapter sources were unchanged; concurrent edits were
confined to distributed coordinator modules outside this execution path. This
is a source-path qualification, not an assertion that the full campaign tree
was frozen.

A ninth constructor-contract test subsequently passed in 0.44 seconds after
the host accepted `PrincipalPolicy` subclasses with `isinstance`. It accepts a
real subclass and rejects an object that merely imitates the policy methods
before journal creation. That test qualifies constructor compatibility only;
it does not execute or qualify a Rust policy binary. Its final receipt is in
`policy-subclass-v3/`; two earlier test-setup failures remain preserved.

The calibrated-window fixture retains raw indications 2 and 4, calibrated
values 5 and 9, and the full covariance matrix `[[5.5,3.5],[3.5,8.5]]`. The mean
is 7 with variance 5.25, matching the existing affine/shared-calibration oracle.
The estimated state is 1.12 with variance 0.84. Shared clock uncertainty and
time/value cross-covariance remain present. SET's replay outcome does not supply
an independent measured reference. The operational comparison therefore remains
`INSUFFICIENT_EVIDENCE`.

The residual fixture uses three native calibrated windows with innovations
0, 0.5 and 10. FDIR/OIT operate on their retained innovations and covariances;
the monitor does not re-estimate state or fabricate another observation. The
last window is a diagnostic drift candidate with ambiguous fault isolation.
Unknown cross-window dependence keeps alarm authority held. A separate native
run with unresolved observability retains the zero-sample CUSUM state and does
not advance it.

The integration checks require every nested scientific grant before another
durable claim, preserve original source/calibration/outcome bytes, reopen the
workflow journal without native re-execution, retain separate human review,
and refuse disclosure after principal revocation. A test-owned copy of an exact
TBRT checkout is deliberately changed to demonstrate tracked-source pin refusal
before import. Negative tests and retained-read tests do not substitute for the
positive native execution evidence.

## Reproduction and evidence

From the ODSP checkout, use an environment containing the versions above and a
stack directory containing the exact seven checkouts:

```sh
CIW_CALIBRATED_WINDOW_STACK_ROOT=/absolute/path/to/stack \
CIW_ACQUIRED_STREAM_STACK_ROOT=/absolute/path/to/stack \
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
python -m pytest tests/test_calibrated_window.py tests/test_residual_monitor.py \
  tests/test_metrology_native_completion.py -q -p no:cacheprovider \
  --basetemp=/absolute/path/to/new-test-scratch \
  --junitxml=/absolute/path/to/evidence/junit.xml
```

The native fixture tests explicitly skip if the operator stack binding is
absent. Such a skipped run does not qualify this lane; inspect the JUnit counts.
`qualification.json` files beneath the integration test's scratch directory
retain synthetic input digests, plans, calibration declarations, actual runtime
identities, outcomes, native numerical results and execution identifiers.

Local evidence is retained under
`/tmp/odsp-metrology-native-completion/`: `runtime-checkouts.json` records all
source-file hashes; `direct-native/` records the existing native test command,
JUnit output, log and before/after source hashes; `metrology-native-v1/` records
the additional principal-bound qualification. These local absolute paths are
execution evidence, not portable deployment configuration. The checkouts use
Git object alternates and require the preserved original object database.
`qualification-index-v1.json` seals the original 63 + 8 native results and
source hashes before later shared subprocess-isolation work. Those receipts
must not be presented as qualification of subsequent adapter changes.

## Current isolation qualification

The current candidate requires explicit host-only companion policies, including
through operational/metrology and replay paths. See
[configuration and replay requirements](COMPANION_POLICY_CONFIGURATION.md).
The native fixtures now supply those policies and probe the real namespace gate.
This host refuses that probe; skipped positive tests do not renew the historical
native qualification above. Existing source hashes and retained policy identity
checks continue to refuse changed runtime replay.

## Remaining limits

These tests qualify local retained-data numerical integration. They do not
establish authentic calibration certificates, traceability to a physical
standard, independent held-out comparison, hardware acceptance, live data
acquisition, state admission, actuation, hosted identity/storage, container
qualification or release readiness. They do not qualify the entire scientific
baseline. Source licences and deployment approval requirements remain intact.
The separate BIM clearance unknown-pose `UNRESOLVED` hold remains unchanged.
Julia remains a research component; this lane neither executes nor promotes it.
