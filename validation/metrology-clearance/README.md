# Candidate validation

See `summary.json` for source hashes, exact wheel identity, executed checks and
limits. The final installed suite includes 51 profile cases and 272 adjacent
cases. Counts across source and installed runs overlap and must not be added.

Executed on Linux / Python 3.12.14 / NumPy 2.4.3. The existing native provider is
unchanged. Original tight-pose synthetic case is held by the pose disagreement
gate; the deliberately uncertain pose case completes with `UNRESOLVED`.

The final noneditable wheel was installed in a fresh venv outside source. The
venv explicitly shares third-party host packages. All 693 wheel package files
match both source and installed bytes. Separate installed subprocess commands
prepared, executed and reopened the retained inspection; a real stdio MCP process
initialized, listed tools, executed the same operation and reused an identical
attempt response. No physical measurements or certificate authenticity were tested.

Reproduce the profile/adjacent suite with:

```sh
python -m pytest -q tests/test_metrology_clearance.py tests/test_agent_protocol.py \
  tests/test_agent_experiment.py tests/test_agent_recovery.py \
  tests/test_measured_thermal_agent.py tests/test_link_study_agent.py tests/test_control_plane.py
```

The tests construct a clean temporary native checkout from the imported CSE tree
and verify its fixed Git tree pin before actual subprocess execution. There are
no successful-provider mocks in the native/MCP journey. Native tests were also
executed: `test_scan_likelihood.py` and `test_clearance_assurance.py` (21 tests,
3 subtests). This is not independent physical or independent geometry qualification.

An intermediate source run found a test fixture with an invalid experiment seal;
the fixture was corrected to reseal its malicious graph so it exercises the
semantic policy rejection. Initial synthetic fixture experiments were held by
coverage and pose gates; those gates were not weakened. The two published fixtures
retain different declared pose uncertainty and remain clearly synthetic.

Hosted Linux Python 3.11/3.13 CI is specified separately and is not counted as a
local passing result. Full-monorepo, Windows, remote MCP and hosted Board
qualification are outside this candidate's executed evidence.
