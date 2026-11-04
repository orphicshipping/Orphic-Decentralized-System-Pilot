# Held sensor and survey reconciliation

This local synthetic journey joins one recorded height measurement with one
retained point from the existing [offline survey flow](../offline-survey/README.md).
It uses an actual `Session`, five retained workbench sources, and the existing
`model.discrepancy.v1` numerical operation. It produces a held JSON projection
and a standalone HTML review page. No device, network service or companion
process is started.

This is one scalar reconciliation in a declared local engineering grid. It does
not estimate a fused physical state, transform coordinates to Earth, calibrate a
physical instrument, authenticate time, admit NSL state, approve a finding, or
qualify a hosted system. Human review remains pending. There is no hold-release
or equipment-control API in this example.

## Source and model contracts

The survey retains the original source definition, capture bytes, dataset and
capture identities, control comparison and assessment receipts. The bridge
reopens that retained report under the current principal policy and checks its
current schema, control, freshness and drift holds. It reconstructs the selected
row from the original capture rather than accepting a copied result value.

The sensor uses the separately preserved Atelier observation-journal candidate,
not a second ODSP acquisition implementation. Two finite recorded arrivals carry
one measurement at source time 990 and receipt times 992 and 995. The journal
preserves both arrivals, retains one measurement identity and reports the second
arrival as a retransmission. A current, read-authorized `readiness(job_id)` query
checks the sticky binding hold as well as job completion. An old completed
export remains historical evidence after a later response loss, but that binding
cannot supply a fresh projection while held. The query grants no execution or
equipment authority and releases no hold.

The host pins `odsp.sensor-survey-model.v1` with model
`coincident-height-discrepancy.v1`. It names the exact survey report/capture/point,
journal export/job/measurement, a complete native marginal covariance artifact,
and an explicit shared additive uncertainty component. Version and source pins
are part of the run evidence. CIW's execution/result identities and numerical
runtime identity are retained separately from those source content identities.

Both observations must declare the same point support, local frame, metre unit,
source clock, time scale and exactly coincident acquisition/source time. Unknown
uncertainty, missing values, uncertain quality, different points/frames/units,
clock differences, expired calibration declarations, source holds and missing
model inputs refuse before numerical execution. No conversion, clock mapping,
interpolation, default uncertainty, covariance repair or implicit independence
is supplied. This deliberately narrow synthetic path does not solve alignment
or clock uncertainty for real instruments.

The synthetic ASSET-A survey height is 10.125 m with declared standard uncertainty
0.012 m. The recorded sensor height is 10.129 m with declared marginal variance
0.0001 m². The model declares a shared additive component of standard uncertainty
0.004 m and sensitivity +1 for each input. Thus, in `[sensor, survey]` order:

```text
joint covariance (m²) = [[0.000100, 0.000016],
                         [0.000016, 0.000144]]
sensor − survey       = 0.004 m, subject to binary64 roundoff
residual variance     = 0.000100 + 0.000144 − 2 × 0.000016 = 0.000212 m²
standard uncertainty  = sqrt(0.000212) ≈ 0.014560219778561 m
```

The marginal uncertainties already include the shared component. The bridge
does not add that component again. These are declared synthetic uncertainties,
not a traceability certificate, probability of correctness or acceptance verdict.
Tests compare the residual, variance and square root against a separate
50-digit Decimal reference.

## Run the source candidates

The ODSP module is [sensor_survey_workbench.py](../../src/ciw/sensor_survey_workbench.py).
Atelier's local `observation_gateway` candidate and its read-only readiness
follow-up are required. They remain unpublished. The earlier missing-Hatchling
build failure has been resolved locally using official-registry, hash-pinned
build dependencies, followed by wheel builds and installed-path regression
checks. That local package evidence does not establish hosted or physical
qualification or turn the candidate into a published dependency.

From the ODSP checkout, using a Python environment with the existing CIW/NumPy
dependencies and both source candidates available:

```sh
PYTHONPATH=src:/absolute/path/to/atelier-candidate/src \
  python examples/sensor-survey-workbench/run.py --output /tmp/new-sensor-survey
```

The output directory must be new; the script refuses an existing path. It writes
synthetic survey and sensor journals under private local directories. The
`workbench` directory contains:

- `model.json`, `survey-context.json`, `survey-raw.json`, `journal-export.json`
  and `journal-readiness.json`: exact source/model inputs and the historical
  readiness observation, never a reusable grant.
- `workspace.json` and `session/`: the actual retained run and CIW numerical
  execution/result occurrence. The two original source artifacts, two native
  observations and joint covariance form an explicit source-dependency graph.
- `projection.json` and `projection.html`: held result and static, data-only
  review page with local source links, units, uncertainty and provenance.
- `manifest.json`: content pins for the complete retained file set.

The recording uses relative time zero for the single selected support. Its
one-second selection container is explicitly a UI/container constraint, not a
sampled duration. Original clock, source time, acquisition/receipt times, point
coordinates and measurement identities remain retained.

Use the `manifest_ref` returned by creation for offline playback into a **new,
separate** scratch output:

```sh
PYTHONPATH=src:/absolute/path/to/atelier-candidate/src \
  python examples/sensor-survey-workbench/run.py --output /tmp/new-sensor-survey \
    --inspect --expected-manifest-ref sha256:THE_RETURNED_MANIFEST_HASH \
    --playback-output /tmp/new-sensor-survey-playback
```

Playback checks exact files, original journal/table provenance, workbench
sources, execution/request bindings and the derived projection. It does not
read an acquisition source, call an issuer or make a new numerical occurrence.
It does not establish current authority, freshness, authenticity or safety.
The operator must obtain a trusted content pin; self-hashes alone cannot
authenticate a file collection. Explicit reprocessing through the live host
checks uses the same source measurement identities and creates a fresh CIW
execution and result. Prior files are never overwritten.

The host provides a fresh admission callback for reads, computation, retention
and disclosure. Both source guards are checked again before computation and
after retention. Saved workspaces never restore these callbacks. A late failure
preserves the local directory and reports an incomplete/withheld outcome rather
than claiming publication or deleting evidence. This is a single-node local
composition; it makes no atomic distributed snapshot or physical fencing claim.

For private operator status without writing playback files,
`inspect_workbench(root, expected_manifest_ref=pin)` now uses the shared pure
`Session.inspect_workspace` validator. Original bridge implementation identities
remain unchanged when an older projection is read. Current source readiness and
recovery guidance are separate in the
[internal operator inspection path](../../docs/INTERNAL_OPERATOR_RECOVERY.md).

## Qualification scope

```sh
PYTHONPATH=src:/absolute/path/to/atelier-candidate/src \
  python -m pytest tests/test_sensor_survey_workbench.py -q
```

The tests exercise real SQLite journals and retained survey storage, the CIW
operation path, correlated uncertainty, explicit replay identities, pure offline
playback, semantic refusal, policy revocation, current holds, file tampering and
I/O/disclosure failures. Company datasets, an authorized physical read-only
instrument, calibrated uncertainty/clock models, authenticated time, NSL
admission, installed-package integration and hosted qualification remain open.
The coordinator cold-restore follow-up is separate; these local directory
artifacts are not yet included in a qualified remote backup/restore boundary.
