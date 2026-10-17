# Installation-aware sensing: bounded first workload

## Decision and product scope

The proposed first product remains a commercial 3-axis MEMS IC on our small PCB,
with temperature and explicit timing. Target roughly 10–15 mm for the embedded
sensor board; use a larger development/probing version as needed. Keep the MCU,
heavy analysis and commissioning reference external for Rev A. The sensor PCB,
its complete housing/mount and the whole deployed instrument have different
footprint constraints. This is not a custom-MEMS die or a manufactured board.

Use one Link application/runtime and NET's existing numerical/evidence machinery.
The differentiating hypothesis is **installation-specific usable measurement
limits with retained evidence and requalification triggers**, not another RMS
score, a claim of unprecedented sensor self-test, or autonomous universal
self-calibration. Commercial viability, cost advantage, patent novelty and
freedom-to-operate have not been established by this source review.

## What the primary sources establish

- PCB Piezotronics describes mounting stiffness/mass and their effects on measured
  frequency response, including reference back-to-back calibration:
  https://www.pcb.com/resources/technical-information/mounting
- Analog Devices CN0549 includes raw acquisition and a characterized mechanical
  mount; ADI describes the XLMOUNT1 configuration as tested to 20 kHz:
  https://www.analog.com/en/resources/reference-designs/circuits-from-the-lab/cn0549.html
- ST FP-SNS-DATALOG2 documents heterogeneous raw capture and common synchronized
  timestamping/labeling; FP-IND-DATALOGMC extends this to motor-control telemetry:
  https://www.st.com/content/st_com/en/technical-documents/DB4865.html
  https://www.st.com/en/embedded-software/fp-ind-datalogmc.html
- NASA's indexed 1989 paper *A self diagnostic system for piezoelectric sensors*
  describes electrical stimulation and resonant-frequency shifts for determining
  certain accelerometer mounting conditions:
  https://ntrs.nasa.gov/citations/19890059609
  The indexed abstract was inspected; a downloadable full paper was unavailable.
- ADXL355 is an example of already-integrated electromechanical self-test,
  temperature measurement and synchronization features, NOT a selected part here:
  https://www.analog.com/en/products/adxl355.html

These sources make blanket novelty claims unsupportable. They do not establish
that a particular competitor does or does not provide every feature of the
proposed miniature OEM product. The supplied SensorDataFabrics page could not be
retrieved, so it is not used as evidence of an industry-wide product gap.

## Identifiability comes first

For a scalar LTI approximation, Y = G_electronics G_sensor H_installation X + N.
With unknown X, multiplying X by an arbitrary nonzero frequency function A and
dividing H by A leaves the output unchanged. A single unknown-input recording
cannot uniquely separate machine, mounting, sensor and electronics responses.
Three axes do not automatically resolve this ambiguity. Multiaxis chains are
ordered matrix products; cross-axis response, rotations and point locations
cannot be treated as independent scalar gains.

The first empirical profile should be **reference paired**: a separately
characterized acceleration reference on the structure side and our assembly on
the sensor side, with declared shared sample timing and gain/phase response.
The raw ratio still includes both sensors' and both electronic chains' responses.
Without trustworthy baseline characterization/de-embedding, label the estimate
relative installed-chain transmissibility, not isolated H_mount.

Reference location and mass loading matter. Two sensors at different points on
a bending plate do not necessarily see the same underlying acceleration. Record
positions, axis transforms, fixture geometry, cable restraint and reference mount.
An instrumented hammer gives force-to-acceleration FRF/accelerance, which is not
the same quantity or units as acceleration-to-acceleration transmissibility.

Internal MEMS self-test applies a known internal force. It does not automatically
exercise or identify every housing/adhesive/magnetic/structure interface. A future
internally driven mount diagnostic needs a characterized force/reaction path,
sufficient excitation and a coupled observability/identifiability argument.

## First executable research kernel

`ciw.mount_characterization` consumes two sets of existing `ciw.observation.v1`
scalar acceleration records. It does not create a new acquisition envelope,
journal, device API, machine controller or registered Session operation.
It is an offline research utility, not a complete installation-qualification
integration. No source evidence is recast as a physical measurement.

The finite profile is H1 with 4–32 disjoint periodic blocks, 16–1024 samples per
block, at most 4096 samples in each stream. It enforces exact declared scalar
units, common frame and sample-time schedule, separate reference/DUT entities,
finite values and equal complete lengths. No resampling, channel rotation,
unit conversion, zero padding, cropping or time-gap repair is implicit.

The H1 estimator uses average(conj(X)*Y)/average(abs(X)^2). Coherence is
abs(Sxy)^2/(Sxx*Syy), calculated over blocks, not one period. Input noise biases
H1; it must be negligible or handled by another explicitly qualified estimator.
The rectangular-window profile assumes coherent periodic blocks with startup
removed by a separate reviewed acquisition procedure. It is not a general-purpose
Welch analysis of arbitrary machine telemetry.

Existing NET observations retain per-frequency relative gain, phase and coherence,
all as estimated/simulated quantities with unavailable uncertainty and null
execution occurrence. Frequency and interpretation remain attached in the review.
The report keeps input/code identities but is not a signed calibration certificate.
Source flags bypass FFT and request review. Weakly excited bins remain undefined;
unknown chain response or relative timing prevents a passing point status.
Reported native input uncertainty remains in the original observations; this
kernel does NOT propagate full joint spectral/calibration uncertainty. It never
substitutes coherence for that missing uncertainty.

A supplied phase-skew bound contributes 360*f*delta_t degrees to the worst-case
phase margin. This is a declared timing budget, not a measured clock qualification.
Different ADC/digital-filter group delays must be characterized upstream.

The output lists **passing sampled frequencies**, not a continuous usable band.
All outputs retain physical qualification HELD and isolated mounting transfer
NOT_IDENTIFIED. High coherence can occur at a mounting resonance, with gain or
phase distortion, so coherence alone never approves installation suitability.
No inverse response compensation, model update or hardware action is performed.

The synthetic compliant example uses the exact base-excited SDOF relation
H=(k+j*c*omega)/(k-m*omega^2+j*c*omega), with an illustrative 512 Hz resonance and
zeta=0.12. This is NOT an Elmer result, an actual PCB modal prediction, or an
empirical sensor/mount model. It exercises the measurement-analysis code only.

```sh
python -m ciw.mount_characterization example --compliant --output request.json
python -m ciw.mount_characterization analyze request.json \
  --input-ref sha256:EXTERNALLY_SELECTED_EXACT_BYTE_DIGEST --output review.json
```

The CLI verifies the exact original request bytes and creates a new report file.
It does not overwrite the request. The in-memory API's optional input_ref is a
host-supplied original-byte reference; structured-content integrity is separately
retained as canonical_request_ref. Offline numerical reproduction is not a
malicious-process sandbox or proof of authentic physical origin.

Method references:
https://community.sw.siemens.com/articles/en_US/Knowledge/what-is-a-frequency-response-function-frf
https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.csd.html
https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.coherence.html
No SciPy runtime dependency is introduced. The kernel uses existing NumPy, and
checks include explicit complex DFT sums independent of the FFT implementation.

## Physical commissioning experiment, not performed

1. Begin with a bench coupon/fixture and a reference whose dynamic response is
   documented in the test band. Use reviewed controlled excitation. Do not loosen,
   drill, strike or modify operating production tooling for this experiment.
2. Record sensor/PCB revision/serial, mount identity/revision, surface, preparation,
   adhesive type/thickness/cure, fastener torque where applicable, orientation,
   cable restraint, reference position and response, temperature and applied
   excitation. Reinstallation is a NEW installation occurrence.
3. Cross reference mounting method, repeat installation, temperature, amplitude,
   excitation frequency and unit/lot. Preserve independently held-out installations
   and units rather than randomly splitting windows from one mounting.
4. Compare model and measurement at the same coordinates/observables. Require
   excitation coverage, gain/phase limits, non-saturation, temporal validity,
   repeatability and propagated uncertainty. A gap in evidence stays unqualified.
5. After commissioning, monitor for departure from the installed baseline at
   comparable operating state. Report installation change suspected or additional
   reference test required, not an unsubstantiated machine-versus-mount diagnosis.

Mount qualification and sensor self-test are separate from operational authority.
Reference/actuator hardware remains in the commissioning kit, not forced into a
10–15 mm board. Temperature can be logged at a slower rate, with a known timing
relationship; a chip's die-temperature sensor is not automatically a calibrated
measurement of the nozzle surface. Add current sensing later through a separately
characterized channel; a signal connector is not permission to connect to mains.

## Solver and data integration

Use Elmer candidates for PCB/housing/fixture modes, preload and thermal-mechanical
changes when the contact/material/boundary model is justified. Use commercial
sensor transfer data where internal MEMS geometry is unavailable. Mechanical
thermal isolation can alter the very transfer path being measured; soft isolation
cannot be presumed compatible with broadband acceleration sensing.

OpenFOAM is optional for an actual fluid/cooling question; no fluid solver is
required merely to characterize an accelerometer mount. SPICE concerns supply,
front-end and timing/noise behavior. Molecular providers only enter where an
explicit material problem warrants them. Blender/Godot/Bevy remain projections
or separately qualified system-runtime participants, never calibration evidence.

The simultaneous solver-profile increment remains a homogeneous steady-slab
input-preparation/reference benchmark. Its Elmer/OpenFOAM bindings are UNBOUND,
its native gate is NOT_RUN, and it is not this mounting experiment's FEM model.
The distinction between a design revision, physical unit, installation occurrence,
model revision, numerical execution and physical qualification remains explicit.

Implementation reuse of the existing Link/ClockSync/measurement-family pipelines
and a full three-axis/spectral-covariance path requires the corresponding owner
integration and qualification; it is not silently added here. No hardware,
closed-loop control, model training, signed release, public data contribution,
repository merge or deployment is established by this local research candidate.
