# Hybrid Actions runners

Status: workflow support prepared; runner registration, activation and hosted
execution must be confirmed separately. This document does not assert that a
self-hosted machine is online or that the failed PR runs have been repaired.

## Allocation

Routine PR checks and the existing Windows/Linux matrices retain their hosted
runners. Three existing workflows can use the dedicated Linux pool:

| Workflow | Jobs on the native pool when enabled |
| --- | --- |
| `proved-heat.yml` | `native-proof`: exact SP1 builds, proof, replay and rejection |
| `interactive-projectile.yml` | `contracts` and `native`: existing Blender/Godot/Bevy ECS investigation |
| `consolidated-integration.yml` | `regression`: source audit and full isolated regression |

The interactive contract prerequisite uses the same pool on an opted-in run so
the native job does not wait for an unavailable hosted prerequisite. Ordinary
PR runs still execute both jobs with their original hosted runner labels.

Set repository variable `NCP_NATIVE_LINUX_RUNNER` to exactly `enabled` only
after the dedicated pool is online. The pool is selected for `workflow_dispatch`
and pushes to `main`. All PRs, release-branch pushes, tag pushes, and all events
while the variable is absent use the original hosted labels. Manual runs on a
reviewed release branch can explicitly exercise the same gate on the native
pool. There is no automatic fallback after a native-runner failure, and no
passing result is inferred from runner availability.

The required runner labels are `self-hosted`, `Linux`, `X64`, and
`ncp-native-linux`. The last is a routing label, not the machine's name or the
Bevy application. Bevy remains a workload installed/built by its existing gate.

## Machine preparation

Use a dedicated Ubuntu 24.04 x86-64 system or isolated VM. The proposed host's
i7-10750H, 32 GB RAM and reported spare storage are sufficient for an initial
allocation; this is capacity planning, not a measurement of an online runner.
A starting VM allocation is 4 vCPUs, 16 GiB RAM, and at least 80 GiB disk. Leave
headroom for the host. The hard preflight requires 7 GiB **available** RAM and
20 GiB free on both the workspace and runner-temp filesystems. The SP1 gate
retains its own resource check and exact compiler, source, ELF and proof checks.

The base image needs Git, Python 3 with venv support, curl, tar, unzip, rustup,
and sudo. Existing workflows install the pinned Python/Rust/engine dependencies;
they must be able to perform their existing noninteractive apt installs and
reach GitHub, package indexes, Rust, Blender and Succinct downloads. Provision
build tools, SSL/clang/protobuf dependencies and noninteractive sudo access in
the dedicated image under the machine administrator's control. Do not use a
personal desktop session containing credentials, private data or equipment
connections as the CI service account. Do not mount the host user's home into
the VM. No GPU/CUDA claim follows from the laptop's RTX 2080 hardware; the
selected projectile gate exercises Bevy ECS, not renderer/GPU qualification.

Use disposable VM images or restore a clean snapshot between jobs. An ephemeral
registration handles one job; provisioning must supply a fresh runner for the
next job, including the interactive workflow's dependent native job. One pool
slot is enough for initial sequential runs, not the repository's full parallel
fan-out. Keep the machine powered and disable suspension for a scheduled gate.

## Registration and activation

Runner registration requires machine access and repository/organization
administration. The connected read-only interface cannot perform it. Follow
[GitHub's runner registration instructions](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners)
from repository Settings → Actions → Runners → New self-hosted runner. Select
Linux x64, use the current runner download/checksum and short-lived registration
token shown there, and add custom label `ncp-native-linux`. Keep generated tokens
on that machine; never paste them into chat, source, logs or the patch. Configure
the runner service with the prepared environment and clean-job lifecycle.

Confirm the runner is **Idle**, is accessible to this repository, and has all
four labels before enabling `NCP_NATIVE_LINUX_RUNNER`. Registration alone does
not start the service. The workflow preflight records the runner name, candidate
SHA, OS, resources and tool paths in `runner-preflight.json`; preflight PASS is
only environment readiness. It does not validate full Git ancestry, installed
native providers, scientific output, CUDA or physical systems.

## Execute and retain evidence

After owner approval and publication of the reviewed patch:

1. Confirm the published branch's exact SHA and the runner's online labels.
2. Enable the variable and manually run `consolidated-integration.yml` on that
   branch. This retains the source audit and isolated regression outcomes.
3. Run `interactive-projectile.yml` on the same reviewed branch; preserve its
   prerequisite, original/replay results, injected-regression detection and
   recovery evidence. Both jobs need an available fresh native runner.
4. Run `proved-heat.yml` on that branch. This is the longer real CPU proving gate;
   retain resource, source, build, proof and rejection receipts. Do not lower its
   thresholds or replace native execution with doubles.
5. Record each run URL, actual `head_sha`, runner identity, conclusion and
   artifacts. Check the branch SHA before each dispatch because it may move.
   A manual run is separate evidence; it does not turn the original PR checks
   green or replace required hosted Windows/platform checks.

The original hosted failures had no runners or steps. Their two failure
annotations still need inspection to resolve the hosted service/account cause.
A self-hosted pool does not guarantee bypass of an account-wide Actions block.
Do not issue mass reruns until a single opted-in gate actually executes.

Disable the variable to restore the existing hosted routing for subsequent
events. This does not move jobs already queued or cancel active jobs. Existing
job commands, timeouts, source pins, skips-as-failure rules, matrices, evidence
retention, and qualification holds remain in force.

## Local checks for routing changes

```sh
python -m unittest discover -s scripts/tests -p test_native_runner.py -v
python -m unittest discover -s scripts/tests -p test_ci_policy.py -v
python scripts/check_ci_policy.py
```

The existing workflow-contract lane runs these tests and checksum-pinned
actionlint. Tests cover insufficient resources, missing tools, wrong platform,
missing preflight, removed activation guards, and accidental PR routing.
