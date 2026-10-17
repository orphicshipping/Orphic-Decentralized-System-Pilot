# Synthetic software checkpoint — 2026-10-17

This evidence supports the metrology software review stack based on PR #232.
It is not physical qualification, normative GD&T acceptance or a deployment release.
[summary.json](summary.json) binds exact changed candidate bytes, numerical runtime,
wheel identities, checks, test-harness corrections and unrun prerequisites.
Compressed JUnit records can be read with `gzip -dc FILE.junit.xml.gz`.

| Check | Result |
| --- | --- |
| Contract-only increment, isolated checkout | 14 passed; wheel built |
| Sampled operations increment, isolated checkout | 55 passed; wheel built |
| Campaign/action increment, isolated checkout | 70 passed; wheel built |
| Selected source regressions | 479 passed, no skips |
| Final focused metrology tests after test-file separation | 74 passed, no skips |
| Isolated installed-wheel regressions | 473 passed, no skips |
| Exact historical BIM/CSE interoperability | 9 passed, no skips |
| Native scan-likelihood fixtures | 12 passed, no skips |
| Source/wheel/installed bytes | All 700 packaged files identical |

Counts overlap and must not be added. Source regression evidence includes the
same contract cases before their relocation into the separate contract test file;
the final focused and installed runs use the final file organization. Two existing
Board SDK tests emit JUnit `record_property` compatibility warnings.

The native scan-likelihood run uses the unchanged CSE tree
`f91c982b580ad4f16a27ea149eedf0e338f6d764`. Existing BIM interoperability uses its
original pinned commit `4b74abda40bba3277de69bf61e9e09283ae2d5b3`, tree
`d1bea6737380b1cdd8f42f9962e282f1047dceec`. Original pinned bytes and the exact
unsigned Git object were recovered and hash-checked locally; no execution pin was
relaxed. These runs still use synthetic inputs.

The final source selection was:

```sh
python -m pytest -q tests/test_metrology_profiles.py tests/test_metrology_review.py \
  tests/test_metrology_remote.py tests/test_metrology_clearance.py tests/test_bim_interop.py \
  tests/test_reference_comparison.py tests/test_board_refit_mcp.py tests/test_agent_recovery.py \
  tests/test_agent_protocol.py tests/test_agent_experiment.py tests/test_measured_thermal_agent.py \
  tests/test_link_study_agent.py tests/test_control_plane.py
```

`NCP_CSE_SOURCE` selects the operator-provisioned native clearance source tree;
`CIW_CSE_REPO` selects the historical pinned quantity integration checkout.
Installed checks copy unchanged tests, needed example fixtures and helper test
modules outside the source checkout; `python -I -m pytest -o pythonpath=` uses the
installed wheel, including real SDK subprocess stdio and HTTP journeys. The installed
selection adds `test_metrology_contract.py` and runs historical BIM interoperability
separately. No package source fallback is used.

See [implementation scope](../../../docs/METROLOGY_IMPLEMENTATION_STATUS.md) for
unmet physical, normative and deployment acceptance gates. No qualification ledger
was updated, no service deployed and no action executed.
