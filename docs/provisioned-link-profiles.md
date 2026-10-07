# Provisioned Link and provider profiles

The native typed-data owner is part of NCP. The Link measurement bridge retains
its native artifact, covariance and stream records through Workbench, preserving
source dependencies, correction revisions and restored execution identities.
`tests/test_link_measurement_workbench.py` exercises the eight positive bridge
cases; the owner and retention tests cover the record boundary itself.

The calibrated and identified-design compositions still require their actual
scientific providers. Prepare the exact committed sources from a local Git
repository containing the original provider history:

```sh
python scripts/provision_pinned_profiles.py \
  --source-repository /path/to/full-ncp-history \
  --profile identified-design --profile calibrated-window \
  --output-dir /private/new-providers
```

The identified-design directory contains eleven roles, including the eight
calibrated-observable roles. The calibrated-window directory contains five roles
and uses its own declared revisions. The preparer refuses missing commits,
existing output directories and modified tracked bytes. Each role has an
independent Git object database; the donor is unchanged. No remote credentials
or replacement implementation are required. A partial failure retains an
explicit failed preparation receipt and is not qualified.

Build the selected wheel, install it with `dev,numerical-checks,legibility` in a
new virtual environment, then use that environment's Python to run:

```sh
python -I scripts/check_link_provider_profiles.py \
  --net-wheel /private/wheels/computational_instrumentation_workbench-0.1.0-py3-none-any.whl \
  --stack-root /private/new-providers/identified-design \
  --window-stack-root /private/new-providers/calibrated-window \
  --output-dir /private/new-profile-proof
```

Run the script from the source snapshot used to build the wheel. It requires
byte agreement between that source, every installed NCP runtime member and the
selected wheel. It copies tests and examples outside the checkout, invokes the
installed interpreter in isolation, checks every selected module executed, and
refuses any skip, failure or error. The receipt records module counts, package
hashes and exact provider revisions; all retained inputs are rechecked after
execution. Use a new output directory for every attempt.

The corresponding `link-provider-profiles.yml` workflow performs the same
preparation and gate from retained provider history. `profiles.log`, `profiles.xml`
and `report.json` remain available when a test fails. The gate covers the
calibrated-observable, identified-design, calibrated-window, Workbench Session,
transport, native typed owner and Link measurement bridge tests together.

These tests execute the real software providers with declared synthetic
fixtures. They verify the acquisition/evidence and numerical integration
contracts; they do not identify an installed instrument, supply a calibration
certificate, or establish product safety/EMC conformity. Real Linux PTY tests
and the GaugeLink environment without NCP are separate platform/environment
profiles because those conditions cannot coexist with an installed Windows NCP
profile.
