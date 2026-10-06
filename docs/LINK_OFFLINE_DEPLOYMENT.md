# Link / NET: reviewed offline Python environment provisioning

This increment extends the existing engineering branch at
`fc138e1dd58c8d9d51849e9191c256b9a7e16758`. It supplies a release-engineering
provisioning tool, not another Link runtime, device profile, package repository,
updater, qualification service or scientific-state store. Existing application
code, numerical methods, schemas, identities, dependency pins and historical
investigations remain unchanged.

## Why this step exists

Previous local qualification used copied host distributions and explicitly
reported differences from the repository's pinned NumPy and cryptography
versions. That route remains available as an honestly labelled development
check. It does not become industrial deployment evidence by passing more tests.

The new route starts with reviewed local binary wheels and exact project metadata,
checks the entire selected dependency closure, retains a hash lock, and provisions
a new isolated environment without a package index or source builds. A customer
appliance should receive that reviewed deployment artifact, not be asked to solve
Python dependencies or install a broker stack. Bootstrap Python and the release
engineer's `packaging` library are still explicit prerequisites; no standalone OS
image, hardware appliance or turnkey installer is claimed.

## Reuse the existing qualification gate

`python scripts/provision_link_environment.py --help` exposes three operations:

1. `prepare`: assemble a **proposed**, create-only bundle from local wheels.
2. `inspect`: recheck its exact selected bytes, format and dependency graph offline.
3. `provision`: require explicit installation permission and create a new environment.

No operation downloads dependencies, builds a source distribution, overwrites an
existing environment, starts NET, opens a device, or grants physical authority.

Profiles select existing project requirements: `runtime` uses the base project;
`link` includes its `legibility` extra; `qualification` includes `dev` and
`legibility`. The script works against the independently selected pyproject and
its matching root wheel. Selected direct project requirements must be exact pins.
Active transitive requirements may specify ranges, but only the explicitly
supplied exact wheel version enters the lock. Missing, conflicting, duplicate or
unselected distributions refuse. Inactive extras (including the existing optional
Git-based provider) do not cause fetching or installation.

### Release-engineering commands

Acquire and review all required compatible wheels on an authorized release host.
Acquisition is separate from the offline script. No supplied hash establishes
publisher authenticity by itself.

```sh
python scripts/provision_link_environment.py prepare \
  --project pyproject.toml --project-ref sha256:REVIEWED_PROJECT_BYTES \
  --wheelhouse /reviewed/wheels --root-ref sha256:REVIEWED_NET_WHEEL \
  --profile qualification --output /new/release-bundle

python scripts/provision_link_environment.py inspect /new/release-bundle \
  --manifest-ref sha256:INDEPENDENTLY_RETAINED_MANIFEST_BYTES

python scripts/provision_link_environment.py provision /new/release-bundle \
  --manifest-ref sha256:INDEPENDENTLY_RETAINED_MANIFEST_BYTES \
  --allow-install --output /new/link-environment
```

The manifest reference is the hash of its **exact bytes**, not a hash read from an
untrusted directory and treated as its own trust anchor. Preparation and
inspection do not issue an installation grant. The script retains the original
pyproject, locked requirements and wheels; writes the preparation manifest last;
and returns `PREPARED_NOT_AUTHORIZED`.

Provisioning returns `PROVISIONED_NOT_QUALIFIED` only after real pip installation,
dependency checks, exact distribution inventory checks and comparison of installed
package files with the selected wheel contents. It preserves the original wheels
in a private input snapshot and records interpreter, provisioner and log hashes.
Installer-generated RECORD files are not compared as though pip had left them
unchanged; source RECORD files remain retained in the wheels. Generated wrappers
and the complete operating-system/shared-library closure are not attested.

Then invoke the existing science/operator qualification, separately:

```sh
/new/link-environment/env/bin/python scripts/check_link_installation.py \
  --wheel /reviewed/wheels/EXACT_NET_WHEEL.whl \
  --output-dir /new/qualification --require-pins \
  --with-scalar-study --with-delivery --with-installation --with-engineering
```

The existing source/installed Linux workflow now performs explicit wheel
acquisition, offline preparation/provisioning, and invokes that same unchanged
pin policy from the new environment. It retains setup logs and hashes even when a
later check fails. Locally unavailable binaries remain a blocker rather than an
excuse to change pins, substitute a source build or label a fixture as NET.

## Trust and failure boundaries

| Threat / failure | Implemented response |
| --- | --- |
| Wheel, project, lock or manifest replaced | Exact external hash selection and semantic reconstruction refuse |
| Original input path changes after validation | pip consumes the exact frozen private copy, not a reopened input |
| Missing or incompatible dependency | Refuse before creating the bundle or environment |
| Wrong interpreter/platform | Refuse this explicitly same-target profile; no guessing or cross-target fallback |
| Archive path traversal, links, duplicate/case-colliding names | Refuse without extracting or importing application code |
| Unbounded archive/dependency declarations | Enforce compressed, expanded, file-count and declaration budgets |
| Startup hooks or wheel `.data` spreading | Refuse; these layouts need a separately reviewed extension |
| Two packages or entry points overlap | Refuse; no accidental overwrite or namespace composition |
| Package replaces Python/pip/bootstrap files | Refuse before package installation |
| Host pip index/target/Python-path configuration | Strip inherited configuration and use isolated, hash-required, wheel-only offline pip |
| Permission absent | Refuse before source reads or environment creation |
| Process failure, timeout or incomplete writes | Preserve partial diagnostics; do not write a success receipt |

The tool uses standard pip with `--no-index`, `--no-deps`, `--require-hashes`,
`--only-binary=:all:`, no cache/source build, and a validated explicit dependency
closure. `PIP_CONFIG_FILE` is set to the OS null device. This is a controlled
installation policy, **not OS-enforced network isolation or a hostile-code
sandbox**. The Python interpreter, pip bootstrap, release host and destination
hierarchy must be trusted. Hashes identify bytes; publisher authentication,
vulnerability review and approval remain separate. A library's RECORD integrity
is not evidence that its code is benign.

The first supported target is Linux on the same declared interpreter/platform
(including the retained marker environment). It does not claim Windows, ARM,
arbitrary namespace-package, `.data`-layout, system-package or cross-kernel image
support. Valid empty package files are preserved. Signed package/model/firmware
review and customer consent are not replaced by this deployment helper.

Source and receipt files are local administrative evidence, not additions to
NET's measurement records or authority registries. Every result explicitly keeps
scientific tests unperformed by the installer, publisher authentication false,
and physical qualification not established. The separate existing qualifier must
succeed, followed by the applicable device/metrology/safety evidence gates.

## Verification scope

`tests/test_link_offline_environment.py` uses synthetic project and dependency
names, not fake NumPy or cryptography packages. Its integration cases exercise
real pip, virtual environments, source replacement, output refusal and a real
process deadline. These demonstrate the provisioning mechanism, not that the
required production dependency binaries have been acquired.

The actual retained NET wheel is also checked for metadata and RECORD integrity;
without its real exact dependencies, complete NET provisioning must refuse.
Existing Link, installation and engineering suites remain required. No scientific
result or qualification status is inferred from the installer tests.

Primary technical references consulted on 6 October 2026:

- pip secure installs: https://pip.pypa.io/en/stable/topics/secure-installs/
- pip configuration / null configuration file: https://pip.pypa.io/en/stable/topics/configuration/
- PyPA wheel format / RECORD and install layout: https://packaging.python.org/en/latest/specifications/binary-distribution-format/

These references describe packaging mechanisms, not Notation certification or
industrial safety qualification. No merge, deployment, flashing, customer-data
upload, machine action or part release is authorized by this increment.
