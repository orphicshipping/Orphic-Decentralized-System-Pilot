# Recorded TCLab temperature example

This example preserves a public experimental recording published by **APMonitor/arduino** alongside its hardware collection program. It exercises archived measurement import and retention; opening it does not contact an instrument or repeat the experiment.

## Pinned upstream material

All three upstream files are copied unchanged from revision `f36e65a70dd7122d1829883899e40e56bf6c4279`:

| Local file | Upstream source | SHA-256 |
| --- | --- | --- |
| `data.csv` | [Hybrid_MIMO/Python_GEKKO/data.txt](https://raw.githubusercontent.com/APMonitor/arduino/f36e65a70dd7122d1829883899e40e56bf6c4279/2_Regression/Hybrid_MIMO/Python_GEKKO/data.txt) | `0be6231c36b8f5b1d05ee999ad7d5ec06cc66a39bc7392c7cdf85d57a5d034d7` |
| `upstream-generate-data.py.txt` | [Collection program](https://github.com/APMonitor/arduino/blob/f36e65a70dd7122d1829883899e40e56bf6c4279/2_Regression/Hybrid_MIMO/Python_GEKKO/generate_data.py) | `f4bec167c7849a0eed808163ec917fff04880ae2ce95d5784edbe7544668d1d8` |
| `UPSTREAM-LICENSE.txt` | [Apache-2.0 license](https://github.com/APMonitor/arduino/blob/f36e65a70dd7122d1829883899e40e56bf6c4279/LICENSE) | `b40930bbcf80744c86c46a12bc9da056641d722716c378f5659b9e555ef833e1` |

The CSV was renamed locally from `data.txt`; its bytes and header spacing are unchanged. The collection source is retained as text for inspection and is not executed by this example. The upstream program has no individual author attribution line; attribution here is to the publishing repository. The upstream license is retained in full.

## Recorded quantities and ordering

The recording contains **201 samples**, spanning **0 to 600.298707485199 seconds**. The adjacent program targets approximately three seconds between samples; the recorded intervals range from 2.990478038787842 to 3.018580675125122 seconds. The final recorded time is preserved rather than replaced by the nominal ten-minute duration.

- `Time (sec)` is elapsed host `time.time()` wall-clock time, not a sensor timestamp, a UTC acquisition date, or a synchronized monotonic clock.
- ` Temperature 1` and ` Temperature 2` are separately queried TCLab sensor-channel readings in degrees Celsius. They are not heater-element or polymer temperatures. Original CSV bytes are retained; these published engineering-unit readings are not raw ADC samples.
- ` Heater 1` and ` Heater 2` contain commanded heater percentages from 0 to 100, not measured watts or independently verified actuator delivery.

The program records a host timestamp, reads T1, reads T2, and **then** issues that row's Q1 and Q2 commands. Same-row temperatures therefore precede the new heater commands. Initial temperatures are queried before the host time origin and assigned time zero. The timestamp does not resolve the separate sensor-read or command latencies. The collection code uses hardware `TCLab()`; it does not select `TCLabModel` or a simulated connection. Its documented stimulus schedule is consistent with the archived rows, but no run attestation establishes that this exact program revision produced this exact file.

## Evidence limits

Instrument serial number, acquisition date, calibration traceability, sensor covariance, clock synchronization uncertainty, and query latency are not supplied. Do not fill these gaps with invented values or zero uncertainty. Git publication timestamps are not acquisition timestamps, and a matching file digest establishes byte identity rather than physical origin or accuracy.

This material supports a recorded-measurement import pilot. A separately specified chronological training/holdout split could test predictive performance on the archived response, provided the held-out observations are not used for fitting or model selection. This example introduces no fitted model or claimed holdout result. It does not establish a new NET hardware acquisition, independent physical validation, causal identification, control qualification, or actuation authority.
