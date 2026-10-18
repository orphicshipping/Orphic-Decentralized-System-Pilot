# Bounded NVIDIA ALCHEMI energy/force pilot

Source implementation: **2026-10-05**. Native qualification: **NOT RUN**.
This guide supersedes the documentation-only status of the initial inventory
for exactly one optional operation: `atomic.lj-energy-forces.v1`.

## Delivered boundary

The [adapter](../src/ciw/nvalchemi_adapter.py) extends NET's existing
`PinnedSubprocessAdapter`; its `operation()` supplies a backend `Operation` for
trusted registration. It does not enter `default_registry()`. The
[contract](../src/ciw/nvalchemi_contract.py) is provider-free. The
[worker](../src/ciw/nvalchemi_worker.py) loads torch/ALCHEMI only during an explicit
request and calls upstream `LennardJonesModelWrapper` with a full directed
neighbor list and `half_list=False` explicitly.

This is a synthetic single-species Z=18 fixture, **not a polymer potential or an
experimental argon calibration**. Epsilon is 0.0104 eV, sigma is 3.4 angstrom,
cutoff is 8.5 angstrom, and switching is disabled. The energy is the unshifted
Lennard-Jones pair sum truncated at the cutoff. Positions are bounded to
+/-100 angstrom, with 2--32 atoms, unique atom IDs, minimum pair separation
0.75 sigma, and a 0.001 angstrom exclusion band around the hard cutoff.

Only non-periodic configurations, explicit units and float64 are accepted.
Stress, mixed chemistry, charge/spin models, learned checkpoints, geometry
relaxation, trajectories, training, domain decomposition, multiple-system
batching and upstream neighbor discovery are outside this implementation.

CPU or `cuda:0` must be selected explicitly. Unavailable CUDA does not fall back
to CPU. Neither path has been natively qualified by this change. In particular,
compatibility, performance and capacity on the intended host and GPU remain
unmeasured. GPU name/capability and torch CUDA runtime are retained when available;
GPU driver version is explicitly unavailable in this pilot, not inferred.

## Scientific records and authority

Atomic inputs are retained in operation parameters and result data with a
canonical SHA-256 identity, provenance label and explicit coordinate frame.
The existing run.v1 source remains session context; the gate uses a clearly
synthetic demo carrier. Its oscillator values are **not** molecular measurements
and are not numerical inputs to the atomistic computation.

Raw successful request/response bytes are retained with hashes. Each execution,
result, prediction and scalar comparison has a separate identity. The outer
execution environment/device is checked against the prediction both at live
completion and during saved-execution validation. Provider failure remains a
refused execution with no manufactured prediction. Structured numerical
mismatches are retained with a failed scalar comparison rather than silently
replaced or promoted.

The independent reference uses scalar all-pairs equations without torch, Warp
or ALCHEMI. A passing comparison establishes agreement between implementations
of these same declared equations, not agreement with nature. Its tolerances
(1e-8 eV / 1e-8 eV per angstrom absolute; 1e-6 relative) are a fixed, provisional
policy, not an experimentally calibrated error bound. Offline diagnostic replay
allows tight floating-point roundoff but preserves policy and status exactly.
`verification_status` remains `not_verified`, `verification_id` remains null,
physical validation remains `not_performed`, and authority remains false.
No uncertainty model is supplied; covariance is unavailable, not zero.

## Provision and bind explicitly

Use a dedicated scientific environment. Install the exact non-editable upstream
VCS revision below, choosing a compatible accelerator stack according to the
[upstream guide](https://nvidia.github.io/nvalchemi-toolkit/userguide/about/install.html).
Do not install every extra or merge UMA's separate environment into this one.
The following identifies source; it is not a hardware-qualified dependency lock:

```text
nvalchemi-toolkit @ git+https://github.com/NVIDIA/nvalchemi-toolkit.git@bf605f9191b2978fc865f4286d11e4481b679999
```

Resolve/install only during explicit provisioning, then retain the environment
lock/wheels and their notices. Neither the adapter nor native gate runs pip,
fetches weights, trains a model or uploads data. The worker's metadata-only
probe requires the VCS origin/revision and a non-editable installed RECORD.
It verifies installed toolkit/ops source-file hashes against RECORD and refuses
modified, unrecorded or linked source files. It fingerprints the full installed
distribution metadata inventory. RECORD is not a publisher signature; third-party
dependency binaries and compiler/driver provenance remain unattested. This is a
trusted-process boundary, not a security or network sandbox.

With NET available in the scientific environment, run:

```sh
python -m ciw.nvalchemi_worker --probe
```

A successful probe produces `python_version` and `dependencies`. A failed probe
produces an explicit refusal envelope, not a usable binding. Inspect that output,
the executable's SHA-256, dependency licenses and the selected hardware. Do not
accept the probe automatically as qualification. Bind the clean NET checkout
containing this adapter at its **complete actual commit SHA**. Pin values are
mandatory and are checked again before and after provider execution.

The reviewed operator binding JSON has exactly these keys:

```json
{
  "net_root": "/absolute/clean/NET",
  "net_revision": "REPLACE_WITH_COMPLETE_NET_COMMIT",
  "python_executable": "/absolute/scientific-env/bin/python",
  "expected_python_sha256": "REPLACE_WITH_EXECUTABLE_SHA256_WITHOUT_PREFIX",
  "expected_python_version": "REPLACE_WITH_PROBED_VERSION",
  "expected_dependencies": {},
  "device": "cuda:0"
}
```

Replace the empty dependency map with the reviewed probe's complete dependency
map. Empty pins deliberately refuse. Preserve the venv interpreter path rather
than resolving its symlink to a system interpreter. Put reports and environments
outside the clean source checkout (the existing adapter permits named venv/cache
directories, but arbitrary untracked source/output files are refused).

**Native gate gap (18 October 2026 source audit):** the earlier command
`scripts/check_nvalchemi.py --binding ...` does not match the current script.
That script now qualifies the separate CPU `nvalchemi.lj-single-point.v1`
binding under `ciw.adapters`, using `--python`, `--revision`, `--repository`
and `--output`. It does not qualify this page's
`atomic.lj-energy-forces.v1` Session operation. Both contracts remain present;
their records, bindings and qualification claims are not interchangeable.

An atomic-profile native gate must still retain a Session workspace/report and
exercise known pair limits, an asymmetric cluster, transformed/permuted inputs,
native force/energy finite differences, fresh execution identities, offline
reopen and explicit missing-runtime refusal. Those are acceptance requirements,
not current execution results. Do not rename the CPU profile or promote its
historical evidence to close this gate. Stress/PBC/MLIP/dynamics/physical gates
remain separate.

For trusted application setup, use `registry.register(adapter.operation())`.
For offline-only reading, call `register_schema()` without constructing an
adapter, then use the existing Session/workspace APIs. Reopening saved files
cannot recreate executable bindings. Explicit replay must bind a provider anew.

## Validation evidence for this increment

Executed locally against the new provider-free contract and worker modules:
**75 passed** with Python 3.13.5 / pytest 9.0.2 on Linux. Coverage includes scalar
energy/force identities, finite differences, translation/rotation/permutation,
Newton balance, bounds/types/units, malformed JSON, local installed-file drift,
real worker subprocess refusal and offline input/model/runtime/record mutations.
No toolkit or toolkit-ops distribution and no CUDA device are available in that
execution environment. The historical increment reported a native-gate invocation
without a binding as **NOT_RUN**, with exit code 2; that observation does not
describe the current script's CLI or supply the missing atomic-profile gate.

The [lifecycle tests](../tests/test_nvalchemi_lifecycle.py) deliberately use a
transport double, not NVIDIA numerical code. The [workflow](../.github/workflows/net-nvalchemi.yml)
defines Ubuntu/Windows tests against the actual repository Session/runner plus
existing operation-runner regressions. Workflow definition is not proof that a
hosted run passed. Full repository/installed-wheel and native numerical results
must be read from their actual execution receipts; they are not claimed by the
75-test local subset.
