# Qualification uses the selected artifact

This follow-on extends the existing Link installation qualifier at
`2c6e317946faed79a48c4097be7f618fd8c696e1`. It does not change any CIW runtime,
scientific operation, measurement schema, dependency pin or authority gate.

## Reproduced boundaries

The qualifier previously checked a wheel against source, later installed from
that same pathname, and finally reopened the pathname for its receipt hash.
Replacing the file after validation could therefore change the bytes pip
installed; a later package check might refuse, but it ran after installation.
Replacing it after installation could also misattribute the final wheel hash.

The new path reads the original once, uses the existing offline provisioner's
complete wheel inspector (archive paths, startup roots, metadata and RECORD),
and checks the existing source/package equality against those same bytes. It
creates an owner-only input snapshot and installs from that snapshot. Snapshot
integrity is checked before and after installation and before the final receipt.
The receipt identifies the original selected bytes, not a late reread of the
source pathname. This is not an atomic filesystem or malicious-host guarantee:
release scripts, Python, pip and the private destination hierarchy are trusted.

The optional `--wheel-ref sha256:...` binds an independently retained selection.
Existing invocations remain supported without it, but explicitly record that no
external wheel reference was checked. CI passes the root reference it already
computed for offline preparation; there is no new download or trust service.

## Installation cannot inherit a different target

The prior qualifier inherited pip environment/configuration settings. Real
synthetic-package tests showed that `PIP_TARGET` and a custom pip configuration
could place the selected package outside the intended qualification environment.
Later checks failed, but did not undo those writes.

The installation subprocess now reuses the existing provisioner's sanitized
environment and explicitly specifies isolated, noninteractive, binary-only,
no-index, no-dependency installation into the selected virtual environment.
Other existing test/command execution behavior is unchanged. This does not
sandbox arbitrary executable code or attest native shared libraries.

## Regression requirements

| Internal requirement | Check |
| --- | --- |
| QA-WHEEL-01 | Replacement or removal of the original source after review cannot change installed bytes. |
| QA-WHEEL-02 | Supplied external selection mismatch and unsafe/invalid archives refuse before environment creation. |
| QA-WHEEL-03 | Snapshot drift refuses; the receipt never hashes a replacement source file. |
| QA-TARGET-01 | Caller pip target/configuration cannot redirect the install outside its selected environment. |

The new tests use explicitly synthetic root packages and genuine packaging,
venv and pip. They intentionally stop after installation/dependency checks and
before NET scientific imports: those tests are mechanism tests, not successful
NET qualification. Full existing Link source and installed test runs remain
separate evidence. No counterfeit production dependencies are used.

```sh
python scripts/check_link_installation.py \
  --wheel dist/EXACT_NET_WHEEL.whl --wheel-ref sha256:REVIEWED_WHEEL_BYTES \
  --output-dir /new/qualification \
  --require-pins --with-scalar-study --with-delivery \
  --with-installation --with-engineering
```

A successful development-mode run still cannot discharge exact-pin, physical
calibration, timing, uncertainty, safety/EMC, publisher or security obligations.

One inherited process-group test emitted no child PID before its 0.5-second
fixture deadline in the full baseline, while passing in isolation. Its test-only
budget is now five seconds, with the descendant still sleeping 30 seconds. The
PID, deadline failure and descendant-termination assertions are unchanged; the
production command deadline is unchanged. This is a fixture timing correction,
not evidence of a production process-control defect.

The existing missing-dependency receipt test now supplies a complete synthetic
wheel via the existing wheel fixture, rather than a deliberately incomplete ZIP
called `absent.whl`. It still injects the same missing-dependency error and
requires BLOCKED status, no command steps and no created environment. This lets
the test reach its intended dependency boundary after full archive preflight.
