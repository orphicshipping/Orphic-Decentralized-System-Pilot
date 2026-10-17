# Native QC capture and retrospective studies

The stream journal, SQLite cache, detector study and outcome bench are additive
instruments built on the current manufacturing reducer and native tool-point QC
contracts. They do not replace `manufacturing_quality.py`, replay historical
snapshots over newer code, acquire live sensor signals, or authorize shipment.

## Installed commands

After installing the NET wheel, the existing `net polymer qc` surface exposes:

- `stream`: append, replay, export and project latched capture findings
- `store`: create-only local SQLite storage, anchored import, append, restart and export
- `detector`: frozen-protocol/complete-manifest retrospective detector evaluation
- `outcome`: deadline-bounded process evidence joined to manufacturing genealogy
  and separately declared outcomes

For example:

```console
net polymer qc stream demo --output capture.json
net polymer qc stream verify capture.json
net polymer qc detector demo --output detector-study.json
net polymer qc detector verify detector-study.json
net polymer qc outcome demo --output outcome-study.json
net polymer qc outcome verify outcome-study.json
```

Synthetic held captures and synthetic studies deliberately return exit 2.
Replay verification returns exit 0 for an intact report; it does not qualify a
physical detector or authorize release. Exit 1 indicates refusal. Output files
are create-only. Use each command's `--help` for explicit specification, head,
commit and protocol anchors. Anchors must be retained separately from untrusted
input to establish the intended history; hashes alone do not authenticate it.

## Integration boundaries

The native `ciw.toolpoint-monitoring-plan.v1` profile is qualified here. The old
`ciw.toolpoint-control-plan.v1` adapter belongs to a separately supplied historical
snapshot. Its source is not copied into this repository and its tests are excluded
from the native gate. Requesting it without that adapter returns a structured
refusal, never an implicit conversion to the native schema.

Store exports preserve the original specification, native updates, receipts,
latched findings and scientific result identity. New execution identities identify
new replay occurrences. Current manufacturing-v2 inspection holds, post-pack holds,
scrap, consumption, assembly ancestry and release gates remain authoritative inputs
to the read-only containment projection. Clean later captures cannot clear them.

Detector statistics and outcome benchmarks remain distinct contracts: the first
uses a complete enrolled detector manifest and independently declared labels; the
second joins enrolled manufacturing serials to deadline-bounded evidence and
separately declared outcomes. Neither tunes thresholds, supplies independent
physical truth, authenticates inspection declarations or promotes synthetic data
to physical evidence.

## Qualification

`check_toolpoint_upgrades_installed.py` requires at least 1,024 native and current
manufacturing tests, zero failures/errors/skips, exact candidate source/wheel/
installed-package byte equality, and actual top-level `net` command journeys from
outside the checkout. It checks both successful replay and refused tampering,
wrong anchors, create-only overwrites and retained holds. SciPy is a separately
pinned numerical test oracle; it is not a new runtime dependency.

The dedicated CI gate runs on Linux with Python 3.11 and 3.13. SIGKILL interruption,
local SQLite locking and filesystem durability behavior are Linux-specific checks;
Windows qualification of these upgrades is not claimed. The manufacturing/QC
foundation retains its separate Windows/Linux gate. Optional legacy-adapter tests
are explicitly deselected, not reported as native passes. Full repository regression
is a separate result from this focused gate.

The older `validation/{stream-quality,durable-quality,detector-validation,outcome-quality}`
records describe original focused-bundle runs. They are retained provenance, not
qualification of this integration. All examples and retained demonstration labels
are authored synthetic records. No live factory, calibrated physical detector,
power-loss storage, authenticated inspector or controller timing qualification is
established.
