# Retained TCLab temperature experiment

This path imports one public APMonitor Temperature Control Lab (TCLab)
experiment into NET's existing investigation workflow. The example retains the
original CSV and a manifest binding its source URL, repository revision,
license and acquisition protocol. It adds a publisher-supplied archived
observation source. Hardware acquisition, source authenticity and an installed
polymer measurement remain separate gates.

The next empirical work stays focused on one temperature acquisition chain.
Successful import establishes that NET preserves this recording's bytes and
semantics. It does not establish the recording's metrological traceability, the
accuracy of a thermal model or the adequacy of an installed measurement.

## Import and inspect

Use the checked-in [original CSV](../examples/temperature-recording/data.csv)
and [source manifest](../examples/temperature-recording/source.json). Import is
offline and uses a fresh output directory:

```sh
net science import-temperature \
  --csv examples/temperature-recording/data.csv \
  --manifest examples/temperature-recording/source.json \
  --output-dir results/tclab --json
net science inspect-temperature --recording results/tclab/recording.json --json
```

Import writes `recording.json`, `run.json`, `workspace.json`, and the Session's
content-addressed `recording-<digest>.json` source copy. The retained recording
binds the source bytes and manifest; the run and workspace make the temperature
channels available through the existing Session. Inspection checks
the retained recording and reconstructs its summary without fetching the
source, contacting an instrument or acquiring another sample. Reprocessing the
archive does not repeat the experiment.

Export a temperature channel as an existing observation stream:

```sh
net science temperature-observations \
  --recording results/tclab/recording.json --channel T1 \
  --output results/tclab/T1-observations.json --json
```

Use `T2` for the second temperature channel. Heater commands are retained as
commands; they cannot be exported by this temperature-observation command.

## Source and timing

The [source attribution record](../examples/temperature-recording/SOURCE.md)
provides the pinned upstream links, exact file hashes, retained Apache-2.0
license, and collection-program evidence. All source assets come from
APMonitor/arduino revision `f36e65a70dd7122d1829883899e40e56bf6c4279`. The
adjacent collection program selects hardware `TCLab()`, but there is no run
attestation linking that exact program revision to the archived CSV.

The archive has 201 rows spanning approximately 600.3 seconds, with
approximately three-second intervals. The adjacent collection program records
host elapsed time, reads T1 and T2 sequentially, then writes the same-row
heater commands; initial temperatures are read before time zero. The timestamp
therefore does not establish simultaneous sensor acquisition or a measured
actuator transition time. The importer keeps the recorded times without
resampling or claiming deterministic timing.

## What the archive establishes

| Archive field | Retained interpretation | Limit |
| --- | --- | --- |
| Time | Source elapsed time in seconds | Absolute acquisition time, clock alignment and clock uncertainty are unknown. |
| T1, T2 | Reported sensor temperatures in degrees Celsius | Neither channel is relabelled as polymer, shell, core or an independent reference. |
| Q1, Q2 | Heater commands in percent | A requested heater setting is not measured heat input in watts or evidence of its physical completion. |
| Source manifest and CSV | Original bytes bound to the reviewed public source and repository revision | A matching hash establishes identity with the retained archive, not authenticated sensor origin or calibration traceability. |

Only the reviewed public byte sequence receives the publisher-supplied
experimental-recording interpretation. A synthetic fixture keeps simulated
semantics; changing an origin label cannot promote it to a measured recording.
Validation refuses altered bindings and malformed or unsupported input instead
of filling missing measurement semantics with defaults.

CSV refusal messages identify the record number (including the header as
record 1) and quantity column where possible. Missing temperature cells stay
null. Nonzero decimals too small for the numeric representation are refused
instead of becoming zero; original source bytes are never rewritten. Choose a
fresh output directory when importing, and a new output filename when exporting;
existing evidence is not replaced. The final timestamp must also permit the
Session's exclusive selection endpoint within its persisted numeric bounds;
this derived value is checked before import creates the output directory.

If a filesystem error interrupts writing after validation, retain that partial
directory for diagnosis and retry into a new directory after correcting the
filesystem problem. A refusal does not establish a completed import; check for
a successful command and a readable `workspace.json` before reopening it.

Device serial, calibration evidence, absolute acquisition time, clock
uncertainty and measurement covariance are not supplied by this example and
remain null. The summary reports physical qualification as `incomplete`, lists
the missing evidence, and keeps the physical-validation authority state
`not_established`. Two temperature channels do not imply two independent
measurements of the same measurand or a held-out validation reference.

## Boundary with the polymer pilot

This archive cannot be passed directly to the [measured thermal
estimator](MEASURED_THERMAL.md). That contract requires already-calibrated
shell, held-out core-reference, heat-power and ambient streams with declared
lineage. This archive supplies neither those channel meanings nor their
calibration and independence evidence. No conversion from heater percent to
watts or from T1/T2 to shell/core is inferred.

The [polymer qualification protocol](TEMPERATURE_QUALIFICATION.md) remains the
gate for the installed pilot: a named sensor/readout and plant, documented
locations and clock, calibration and uncertainty evidence, predeclared
criteria, an appropriate independent reference, held-out experiments and
review. This importer does not close device-gateway or physical-control
acceptance gates.
