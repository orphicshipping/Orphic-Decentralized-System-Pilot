# Superrepo debugging audit - 2026-10-04

Baseline: `5878e0c97147fc03d1f24f946ca12b0b045c861f`.
This is a bounded audit of shared source verification, qualification orchestration,
release evidence and retained dependency handling. It does not qualify every
scientific provider or establish physical accuracy.

## Reproduced defects and repairs

| Boundary | Reproduced behavior | Repair and regression evidence |
| --- | --- | --- |
| Dependency projection | A refused request containing missing or malformed result references broke dependency inspection and could prevent a journal-bearing workspace reopening. | Project request-derived edges only from completed occurrences. Retain refused parameters, execution identity, evidence edge and outcome unchanged. Eight new cases exercise refusal/save/reopen and completed downstream staleness. |
| Source verification | A staged index change during the working-byte scan could escape the initial index check. | Compare the index again after the scan; retain the final HEAD check. Tests mutate the index and HEAD during verification. |
| Source verification cost | Each imported file was read once in its module and again in the full Terminal audit. | Verify exact imported trees and ancestry against one captured HEAD, then freshly hash the complete checkout once. Batch imported-path index/untracked queries; retain original refusal checks. No cross-invocation cache is introduced. |
| Qualification startup | Failure to obtain HEAD occurred outside the failure-report boundary, leaving a previous success report visible. | Retain a fresh failed occurrence with an unknown revision when initial Git inspection fails. |
| Child launch | Failure to start a qualification child omitted the attempted lane and command from the aggregate report. | Retain the command, failed lane, null exit code and exception before returning failure. |
| Child evidence | A report or directory symlink at a fresh output path could reuse older evidence. | Refuse linked child evidence paths and preserve the older bytes. |
| Tool selection | Relative PATH entries, relative compiler settings and Windows implicit-current-directory lookup could inspect the caller's tool instead of the child gate's tool. | Resolve lookup against the declared gate directory, preserve explicit CLI paths, and refuse selected tool bindings that depend on a different execution directory. Test real Windows lookup with current-directory search enabled and suppressed. |
| Tool prerequisites | Invalid encoded version output escaped the doctor's structured refusal path. | Retain a refusal identifying the decoding failure, without changing qualification status. |
| Release evidence | Two required artifact names could share one artifact ID in an offline snapshot and still receive `ci_pass`; JSON exponent overflow could create infinity. | Require unique positive artifact IDs, matching online pagination validation, and refuse nonfinite parsed numeric values. Positive extra artifacts and finite exponents remain accepted. |

The full Python CI matrix now retains JUnit results, reports the twenty slowest
tests, and emits a stack dump if a test runs longer than five minutes. The
five-minute setting is diagnostic; it does not skip or terminate the test.
Workflow triggers, matrix entries and scientific acceptance requirements are
unchanged.

## Scoped validation

These checks ran with Python 3.12.13 on Windows. Counts cover distinct scoped
sets, not the full repository suite.

| Set | Result | Boundary |
| --- | --- | --- |
| Dependency refusals, correction journal/session and legibility | 103 passed | Includes failed-request retention and save/reopen without recomputation. |
| Release evidence and CI policy | 67 passed, 177 subtests passed | Offline metadata validation and policy checks; not artifact authentication. |
| Qualification coordinator and prerequisite doctor | 100 passed, 1 skipped | The skip requires a POSIX npm launcher. Real Windows executable lookup is exercised; the Python 3.11 compatibility and POSIX relative-launch branches are simulated under this Windows Python 3.12 interpreter. |
| Source verification, focused full-checkout and compact cases | 19 passed, 1 skipped | POSIX working-tree executable bits are unavailable on Windows; staged mode drift is covered. |
| Measured thermal, existing thermal, interactive and workbench sessions | 107 passed, 13 skipped | Skips require the exact external calibrated/identified provider stacks. |

An isolated comparison over the same immutable 21-module checkout returned the
same import results before and after the source-audit change. Tracked-file reads
fell from 6,902 to 4,145; bytes read from 165,921,932 to 91,258,980; and Git calls
from 166 to 107. No cross-call cache is used. One timing pair was faster, but did
not control filesystem warmth or concurrent load and is not a speed guarantee.

Independent review covered the changed dependency, release, source-verifier
and prerequisite-tool selection boundaries. Clean-commit source audit and publication receipts are retained
separately because they bind the resulting commit.

## Verification boundaries

- The imported module manifest, nine runtime manifests, package requirements,
  `session.py` and the original thermal reference remain byte-identical to the
  baseline (13 checked files in total).
- Refused executions still have an execution identity and no result. A dependency
  node marked current is not a scientific pass or permission to act.
- Source checks retain exact bytes, file types, modes, Git ancestry and runtime
  bindings. These checks are observations of a local checkout, not authenticated
  ownership or an atomic filesystem snapshot.
- The prerequisite doctor remains read-only and does not qualify a build.
- Release metadata checks establish neither artifact authenticity nor numerical
  or physical correctness. Offline snapshots remain unauthenticated.

## Remaining gates

Private-provider workflow failures at the baseline stopped at credential setup:
`CIW_PROVIDER_READ_TOKEN` was unavailable to those jobs. No provider tests passed
in those lanes, and this patch does not change token scope, access policy or pins.

The baseline's Ubuntu Python 3.11 and 3.12 full suites each completed with 7,350
passed, 903 skipped and 236 subtests passed; Windows suites remained running at
the observation. These baseline results cannot qualify the changed candidate. Candidate-specific regression and
source-audit results accompany the publication report. Physical measurement,
calibration authenticity, held-out reference independence and human operational
acceptance remain separate obligations.
