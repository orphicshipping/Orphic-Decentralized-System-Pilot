# Link scalar developer-kit investigation

Extends PR204's existing DeviceRuntime/Journal, Session, typed DAG, covariance
contracts and MCP. It does not create another device OS, store or control plane.

## Receiver

`net link capture-serial` opens one explicitly selected Linux TTY and configures
an explicitly selected 8N1 baud rate. It requires `--allow-read`, never polls,
and transmits no instrument command bytes. `--allow-pty` additionally permits
pseudo-terminal tests, which are always labelled synthetic. No discovery,
automatic reconnect, remote synchronization, flashing or control is added.

Opening/configuring serial hardware can affect its control lines. A reviewed
RX-only interface, appropriate isolation/protection and a specific documented
instrument interface are necessary. Do not connect RS-232 voltage to MCU GPIO.
Only Linux PTYs have been exercised, not an actual USB serial chipset/gauge.

Each original chunk is committed before interpretation. OS/driver buffers can
overflow; a crash between read and SQLite commit can lose in-flight bytes.
The implementation does not claim complete or exactly-once physical acquisition.
No acknowledgement is sent to the instrument. Storage failure refuses while
preserving the already committed prefix. A reconnect starts a new explicitly
selected session/boot. Existing local `net link sync` handles delivery retries.

## Four actual operations

1. `link.decode-scalar.v1`: one opaque channel, contiguous chunk sequence from
   zero, 1..32 complete CRLF lines, existing exact scalar/unit grammar. Original
   stream, line byte ranges and contributor ranges are retained. Gaps, overflow,
   malformed data, wrong units, changed context/procedure and trailing partial
   records refuse. No resynchronization or source repair is performed. The
   operator must arrange capture at a message boundary; syntactically valid
   truncation before recording cannot be detected from the bytes alone.
2. `link.apply-affine.v1`: `y_i = a*x_i + b`, within a declared input range and
   the same input/output unit. This is a declared additional post-report
   correction, not a fit, the instrument's internal ADC calibration, or proof
   of improved accuracy.
3. `link.mean-model.v1`: classical arithmetic mean and residual against an
   explicitly exact mathematical reference, with a nominal absolute-limit
   comparison. Inspection qualification remains HELD.
4. `link.audit-scalar.v1`: independent Fraction arithmetic checks the affine,
   full first-order covariance and mean calculations. The kernels use Decimal
   arithmetic with one binary64 rounding per output. This audit does not
   independently validate the decoder, certificate or physical assumptions.

Each stage executes through the existing CapabilityRegistry/DAG/Session. Exact
retained dependencies reuse two files from PR184 head
`4265eb2382d694f07fd22e1701c107f992297b42`, without implementing another hook:
`operations/schemas.py` blob `ada8eb2ab7893dec186e9034b204638d5a0ea0ac` and
`operations/runner.py` blob `70b7d231652b61349431f29dea2554cde29c87ea`.
This is not a merge of all PR184 functionality. Parent integration must preserve
PR185's source-dependency checks as well as these exact-result gates.

## Covariance, calibration and timing

A supplied `covariance-artifact.v1` binds ordered variables
`[line0:input,...,lineN:input,gain,offset]`, units, reference values, frame,
capture identity and calibration basis. Full inter-reading and coefficient
correlations are retained. `J_i=[0,...,a,...,0,x_i,1]`, `C_y=J C_joint J^T`,
and mean variance is `1^T C_y 1/n^2`. No missing-to-zero or implicit independence.
This is conditional first-order propagation, not a universal uncertainty budget.
Timing, drift and model discrepancy are not included unless explicitly supplied.

Original certificate bytes remain in the existing Link calibration declaration.
Its half-open interval is assessed against supplied acquisition time. A receipt
is not acquisition time, so serial captures normally retain
`ACQUISITION_TIME_UNKNOWN`. The calculation stays inspectable but calibration
traceability and physical inspection remain unqualified.

Ticks use the host monotonic nanosecond representation, not a nanosecond-accuracy
claim. A line retains its final contributing chunk's receipt tick. Lines received
in one chunk can have indistinguishable times. Native export withholds unknown,
duplicate or unrepresentable times instead of inventing offsets. All decoded
rows remain retained. Corrected physical-origin outputs are estimated, not new
physical samples. Native per-row covariances are explicit marginals; the full
matrix and native/line/covariance-index mapping accompany them.

Exports use existing observation-stream and covariance schemas, not a new
`net data` family. Keep the original capture/request/workspace and mapping with
the exports. PR185 Workbench/data bridges remain unchanged.

## Operator journey

```sh
net link study example --output scalar-request.json
net link study run scalar-request.json --output-dir scalar-study
net link study inspect scalar-study
net link study replay scalar-study --output-dir scalar-replay
net link study export scalar-study --output-dir scalar-native
net link study report scalar-study --output scalar-panel.html
net link study mcp scalar-study
```

The final command uses the existing MCP stdio Server and AgentHost, with frozen
alias `investigation` and zero execution grants. After normal MCP initialization,
`net_inspect` can select `results/<result-id>/data/mean`. No new agent protocol,
device-control permission or public listener is introduced. HTML is offline.

For an operator-reviewed opaque manifest (clock ID `link.host.monotonic_ns`,
tick_hz `1000000000`, event `instrument_report_receipt`, synchronization and
clock uncertainty explicitly null):

```sh
net link capture-serial /dev/ttyUSB0 --manifest manifest.json \
  --journal outbox.sqlite --create --channel CH0 --baud 9600 \
  --duration 10 --allow-read --output serial-receipt.json
```

The receipt parent must exist and all output paths must be new. The optional
private GaugeLink SBC application builds a reviewed profile using the public
SDK and preserves an original procedure reference; it owns no second journal.

## Qualification and missing hardware

```sh
python -m pytest -q tests/test_link_study.py tests/test_link.py tests/test_connectivity.py
python -m pip wheel --no-deps --no-build-isolation . --wheel-dir dist
python scripts/check_link_installation.py --wheel dist/<wheel>.whl \
  --output-dir <new-directory> --require-pins --with-scalar-study
```

The scalar serial gate is Linux-only. Without the new flag, the existing Link
installation gate remains unchanged. `--allow-host-dependencies` explicitly
records a non-pinned local run and never closes the exact-pin gate.

Still required: physical gauge/cable and independent reference, SBC/ARM execution,
measured electrical/timing/thermal compatibility, secure device enrollment,
remote fleet services, MCU firmware, FPGA/AFE implementation, full monorepo
regression and production qualification. These software tests do not establish
a shippable or physically qualified developer kit.
