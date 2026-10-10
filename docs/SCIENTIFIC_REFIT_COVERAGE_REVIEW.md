# Scoped refit reference coverage

This review adds the existing Board and GaugeLink command surfaces to the
scientific coverage audit's reference index. It does not qualify their science,
execute their tests, change their validation rules, or accept any candidate.

## Reviewed source

The review inspected source at
`836aed697e7890938dcbcf71ee687e52697ecca1` against base
`751d25cdc3b5210fd115ab92a41cb4330e429573`. The command catalog, coverage audit,
and coverage tests have identical Git blobs at both revisions. The two command
entries already exist; their reference declarations and the 64-command test
count had not been updated. The current inventory has 59 root commands and
seven catalog additions, for 66 commands.

## Reference declarations and their bounds

- `net refit`: entry point `src/ciw/board_refit_cli.py`, primary reference
  `tests/test_board_refit.py`. The suite specifies retained baseline/candidate
  identity, scoped grants, target bounds, result reuse, exact retries, and
  refusal after interrupted execution. Its complete-journey test requires
  `accepted: false` and `review_status: unreviewed`. CLI setup, byte pinning,
  and revocation have additional coverage in `tests/test_board_refit_cli.py`;
  persistence and concurrent retries are covered in
  `tests/test_board_refit_review.py`. The documented oscillator profile does
  not authorize physical control or establish physical equivalence between
  compared channels. See [Board refit](BOARD_MCP_REFIT.md).
- `net gaugelink-refit`: entry point `src/ciw/link_refit_cli.py`, primary
  reference `tests/test_link_refit.py`. Its independent Fraction reference
  includes the full covariance and signed cross terms, with a declared
  eight-ULP arithmetic criterion. Other cases cover missing covariance,
  source/model/audit binding mismatches, bounded offset scale, unchanged
  original occurrences, and unaccepted candidates. The CLI/MCP journey is
  separately specified in `tests/test_link_refit_cli.py`. Scaling the
  hypothetical offset-error amplitude at fixed means and correlation is not
  a measured uncertainty reduction or a new calibration. See
  [GaugeLink refit](GAUGELINK_MCP_REFIT.md).

These are existing test references, not new test-run evidence. The Board MCP
workflow is configured to run the domain and CLI suites in source and installed-wheel modes;
that workflow's existence does not establish a result for a new candidate.
Coverage regression tests verify both exact reference pairs and refusal when
either declaration is removed.

## Unchanged evidence boundaries

The coverage report retains `tests_executed: false`,
`qualification: not_performed_by_audit`, `physical_validation: not_established`,
and no execution or state-admission authority. Referenced-file existence alone
is not scientific acceptance. GaugeLink's physical hub remains
`NOT_ESTABLISHED`; retained candidates remain unreviewed and unaccepted.

No physical experiment, installed-chain qualification, acquisition, remote
activation, or Julia execution is performed or inferred by this change.
Physical-performance claims require separately specified installation and
calibration evidence, a defensible uncertainty model, independent held-out
comparisons, and review for the intended use under
[Model validity and evidence](MODEL_VALIDITY_AND_EVIDENCE.md).
