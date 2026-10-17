# Link deployment: startup-import and process guards

Follow-on to [offline environment provisioning](LINK_OFFLINE_DEPLOYMENT.md),
based on `dab59df685dfd2f4afae15a044e889f9f889240d`.


This follow-on repairs two bounded deployment controls; it does not change the
Link/NET runtime, qualification policy, package pins or bundle schema.

### Threat and reproduced behavior

The selected wheel is an untrusted executable artifact until independently
reviewed. Its filename, self-consistent RECORD and external content hash do not
make its importable contents safe. The previous check rejected `sitecustomize.py`
but accepted `sitecustomize/__init__.py` and native-module spellings. Python's
startup hook is an import by module name, so that distinction is not safe.

The new member check reserves the entire top-level `sitecustomize`,
`usercustomize` and `pip` import roots, including packages and ABI-tagged native
module names. Ordinary similarly named library modules remain supported. Bundle
inspection and provisioning reapply the check: a previously prepared unsafe
bundle cannot pass merely because its manifest hash is unchanged. No module is
imported to classify a wheel.

A harmless reproduction installed a synthetic startup package successfully under
the original provisioner. On the local test host it did NOT run at ordinary
startup: the host's `/usr/lib/python3.13/sitecustomize.py` precedes the installed
package. A separate explicitly isolated `site.execsitecustomize()` probe with the
candidate package first on the search path executed its scratch-marker payload.
That probe demonstrates the import mechanism, not exploitation of this host's
normal installer or any customer system. Native module variants are rejection
fixtures, not compiled native exploit tests.

The previous command runner wrote stdout/stderr directly to disk and checked the
8 MiB read limit only after the process completed. A process could therefore
write past the intended diagnostic budget before a refusal was raised. It now
drains a pipe in bounded chunks, retains at most 8 MiB per command, and fails
immediately on the first excess byte. The retained file is an exact prefix, not a
complete transcript when overflow occurs. The owned process group is terminated
on overflow, timeout, monitor/read/write failure or caller cancellation; the
immediate child is waited for. Logs are create-only with mode 0600, and stdin is
closed to interactive input.

The deadline covers both process termination and pipe EOF. A child retaining the
pipe after its parent exits cannot turn an unfinished command into a completed
one. Conversely, EOF alone does not establish that the process has terminated.
These are Linux process-group controls, NOT a hostile-code sandbox: descendants
that escape the group, arbitrary files written by an executed program, native
library side effects and whole-host resource use require separate containment.
Bootstrap Python, pip and the release host remain trusted. A failed command
leaves diagnostics and partial files but no success provisioning receipt.

### Regression requirements

| Internal requirement | Executed regression basis |
| --- | --- |
| LD-STARTUP-01: reject reserved startup/bootstrap import roots before creating a bundle or environment | Package and native-name cases; old-bundle reinspection; similar nonreserved names accepted |
| LD-LOG-01: bound diagnostic disk writes while the command is running | stdout/stderr overflow, empty/exact-bound output, real 8 MiB ceiling, unchanged original bundle and absent success receipt |
| LD-PROC-01: retain failures and stop owned commands on deadline/cancellation | Real subprocess, inherited pipe, early EOF, nonzero exit, monitor cancellation and create-only private logs |

The existing empty `py.typed` test was corrected to select the exact synthetic
distribution's marker. Selecting the first `py.typed` anywhere in a virtualenv
could inspect pip's nonempty marker instead; this was a test-selection defect,
not a failure to install the intended empty file. The assertion remains exact
byte equality and now also requires exactly one matching owned marker.

Reference semantics: Python's `site` startup imports and `subprocess` lifecycle,
and PyPA's binary wheel format and secure-install guidance. These tests are not
a security certification, independent penetration test or evidence that all
possible startup behavior has been excluded.

- https://docs.python.org/3.13/library/site.html
- https://docs.python.org/3.13/library/subprocess.html
- https://packaging.python.org/en/latest/specifications/binary-distribution-format/
- https://pip.pypa.io/en/stable/topics/secure-installs/
