# Link: reference-paired installation investigation

This is a bounded research workload on the existing **unified Link** runtime,
not another acquisition service or a self-characterizing physical sensor.
Parent: PR #209, `128436b99bb45cb0a7904de1df6dab012173952d`.

The approved research handoff proposes characterizing the complete installed
measurement chain while keeping the first MEMS board focused. Its literature
basis is a supplied secondary review, not independently validated measurements
of the Notation board. No source-paper response, standards compliance, or
commercial performance claim is imported as our own qualification evidence.

## One concrete investigation

```text
Exact installation plan and explicit point-review criteria
  -> reference/DUT observations through the existing Link contract
  -> original outbox and ingress journals
  -> link.characterize-installation.v1 in the existing NET Session
  -> relative installed-chain response and retained limitations
  -> offline inspection, replay, original-byte recovery and read-only MCP
```

`net link installation demo --compliant --output-dir ../installation-demo`
executes this chain with synthetic observations and two distinct local journals.
It creates `installation.html`; it opens no port and produces no physical
excitation. The same command without `--compliant` uses a synthetic unity chain.
The numbers in these examples are **authored software fixtures**, not properties
or acceptance limits for our actual PCB, mounting fixture or reference sensor.

The compliant fixture uses 1,024 samples/s, four 32-sample periodic blocks per
channel, and a synthetic 384 Hz resonance. At that resonance coherence is near
one but relative gain is about 4.285. Only the 32 and 64 Hz sampled points meet
the example's declared gain/phase/coherence limits. This is not a continuous
qualified 0-64 Hz band. No interpolated band, isolated mounting diagnosis,
inverse correction, confidence probability or calibrated uncertainty is issued.

## Separate the physical unit from the installation

The plan binds a physical DUT unit and its hardware revision, a separate reference
unit, an **installation occurrence**, mounting revision and exact fixture,
mounting and procedure artifacts. It declares reference/DUT locations, temperature
and what that temperature describes, the Link manifest/boot/channels, and the
analysis criteria. Die, PCB, surface and ambient temperature are not substituted.
These remain operator/source declarations, not hardware identity attestation.

The exact plan-byte reference is carried by every original observation's existing
`procedure_ref`. The existing context binds the asset. Replacing the mount,
hardware, criteria, temperature declaration or plan bytes without new appropriately
bound observations refuses instead of relabeling the old acquisition. This does
not prove when a plan was authored or prevent a malicious producer from fabricating
new records. Original sample provenance is not authenticated by a hash alone.

`applicability` compares a proposed plan with the original retained plan. Changes
produce `RECHARACTERIZATION_REQUIRED`; equal declarations produce
`DECLARATIONS_UNCHANGED`. Neither transfers qualification or proves the physical
installation is unchanged. It runs no experiment and creates no new scientific
execution. A real remount requires a new installation occurrence; a replay does
not create a new sensor, installation or measurement.

## Preserve the current observation and timing contracts

This first profile admits exactly two enabled, scalar acceleration channels from
one explicitly bound Link manifest and boot, with separate transducer identities.
They must use `m/s^2`, the declared one-axis frame, and a sensor-sample clock, not
an instrument-report or host-receipt timestamp. The paired sample ticks must
agree and form a uniform, exactly representable integer-tick schedule.

The plan specifies block length **and number of replicate blocks**. A shorter
recording cannot become an accepted analysis merely because it still contains
four complete blocks. Supported block lengths are 16 or 32; there must be 4-8
blocks and at most 256 total reference+DUT observations, honoring the unchanged
Link capture limit. This is a finite integration profile, not a production-rate
vibration waveform driver. Higher-rate/longer acquisitions need an independently
specified existing-substrate extension, not a silent increase to these limits.

There is no channel selection after the fact, cropping, interpolation, unit/axis
conversion, clock alignment or invented sample. Gaps, acquisition events, missing
values, unfaithful native time projections and mismatched timing refuse analysis.
Source flags remain attached to their original measurement identities. A flagged
source is retained with `SOURCE_REVIEW_REQUIRED` and never enters the FFT.
Unknown chain response or relative timing allows a clearly limited response
preview but does not produce passing sampled frequencies.

Original marginal and full joint uncertainty records remain in the exact capture.
They are **not propagated to spectral uncertainty** by this workload. Coherence
is a consistency diagnostic, not a calibrated confidence or uncertainty measure.
A matched-chain declaration requires its supporting bytes; their presence does
not authenticate a certificate, establish reference traceability or remove the
reference/electronics/sensor dynamics from the relative response.

## Reuse the existing computation, do not invent another numerical engine

The `mount_characterization.py` implementation and its 37 tests are adopted
unchanged from `NET-Solver-and-Mounting-Workloads.zip`. Source SHA-256:

`cc8491836c242c42df6b236e0fde814422b7faa072804c7c448c973d39e7217d`

Its estimator is the existing one-axis H1 periodic-disjoint-block method. See
[MOUNTING_CHARACTERIZATION.md](MOUNTING_CHARACTERIZATION.md) for its assumptions.
The original tests include a separate direct complex-sum calculation and a
mass/stiffness response check. They are numerical software tests, not independent
reference hardware experiments.

The new operation uses the existing CapabilityRegistry, OperationRegistry, typed
graph, source evidence, Session execution/result records and payload validation.
Its nested kernel result remains byte-for-byte the result produced by that kernel:
it does not acquire a fabricated execution ID. The **outer Session** records the
actual registered computation occurrence. Recalculation during payload validation
is consistency checking, not an independent proof or empirical validation.
Formal `verification_id` remains null and the inner kernel's original scope stays
unchanged. No SP1 proof, training admission, physical control or release is granted.

## Retained lifecycle and engineering commands

The study envelope carries the exact selected plan and capture bytes. It chunks
the capture into existing bounded packed-byte objects so shared JSON string limits
are not weakened. It is experiment metadata, not a replacement observation schema.

```sh
net link installation prepare --plan plan.json --plan-ref sha256:PLAN_DIGEST \
  --capture capture.json --capture-ref sha256:CAPTURE_DIGEST --output study.json
net link installation run study.json --request-ref sha256:STUDY_DIGEST \
  --output-dir ../investigation
net link installation inspect ../investigation --request-ref sha256:STUDY_DIGEST
net link installation replay ../investigation --request-ref sha256:STUDY_DIGEST \
  --output-dir ../replay
net link installation restore ../investigation --request-ref sha256:STUDY_DIGEST \
  --output-dir ../originals
net link installation report ../investigation --request-ref sha256:STUDY_DIGEST \
  --output ../installation.html
net link installation applicability ../investigation --request-ref sha256:STUDY_DIGEST \
  --plan next-plan.json --plan-ref sha256:NEXT_PLAN_DIGEST
net link installation mcp ../investigation --request-ref sha256:STUDY_DIGEST
```

External references mean trusted selection of exact bytes, including formatting;
a hash read from an untrusted directory cannot authenticate that directory.
The reader checks and uses the same request bytes, and validates the original
workspace and graph occurrence bindings. Outputs are create-only. Restore recovers
original plan/capture/request bytes; replay creates a fresh computation with the
same physical evidence. This is a coherent bounded read, not an atomic filesystem
snapshot or malicious-process sandbox.

MCP uses the existing AgentHost/stdio server with the exact already-validated
workspace bytes, the existing document limit and **zero execution grants**.
The advertised operation remains unbound to that agent. No new network listener,
account service, certificate enrollment, private-data contribution or gateway.
All HTML, artifacts, journals and Advanced views are customer-local, potentially
sensitive scientific evidence, not approved cross-customer exports.

## Internal requirements and executed-test mapping

| Requirement | Principal test |
| --- | --- |
| LI-001: reuse the original estimator and native source ancestry | `test_existing_h1_kernel_used_unchanged_and_native_ancestry_preserved` |
| LI-002: remount/changed criteria cannot relabel old acquisition | `test_changed_plan_cannot_relabel_existing_acquisition` |
| LI-003: sample plan must be complete, paired and time-faithful | Timing/source discontinuity and predeclared block-count tests |
| LI-004: flags cannot disappear or enter FFT interpretation | `test_flags_preserve_data_and_prevent_fft` |
| LI-005: replay is computation, not new measurement | `test_replay_is_new_computation_not_new_measurement` |
| LI-006: applicability review transfers no qualification | Applicability and unchanged-plan tests |
| LI-007: original bytes recover exactly | `test_exact_original_plan_capture_and_request_roundtrip` |
| LI-008: MCP remains a frozen, read-only evidence interface | Actual stdio initialize/inspect/denied-execution and snapshot tests |

The existing Linux 3.11/3.13 source/installed qualifier includes the workload with
`--with-scalar-study --with-delivery --with-installation`. Exact repository pins
are unchanged. A local run using `--allow-host-dependencies` is explicitly not
exact-pin or supported-release qualification. The delivered source closure is
not a complete monorepo checkout or combined qualification of pending data,
measurement, account/consent, pinwheel, thermal-solver or private product branches.

A real reference sensor, its chain calibration, measured timing/phase behavior,
qualified fixture and excitation procedure, actual instrument data and full
spectral uncertainty remain necessary for physical qualification. No hardware
actuation, hot/pressurized tooling experiment, firmware flashing, cloud transfer,
model promotion, machine control, merge or deployment occurs in this increment.
