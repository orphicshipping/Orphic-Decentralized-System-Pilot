# Notation Device Runtime, Link Protocol and NET ingress

## Delivered scope

The named investigation is **GaugeLink recording to a retained inspection
observation**: an explicitly bound device manifest and finite original byte
stream enter a durable local outbox, synchronize to a separate local ingress
cache, execute `link.project-observations.v1` through the existing NET Session,
and export existing native observation/covariance records and a read-only
Dashplate. The implementation extends the [gateway proposal](DEVICE_GATEWAY.md)
and [acquired-stream boundary](ACQUIRED_STREAM.md); it is not a new scientific
ledger, provider scheduler, apparatus controller or canonical-state admission
system.

**This is an executable software/reference implementation.** No physical caliper,
force gauge, ADC, MCU board, FPGA, radio, camera or survey receiver is qualified.
The portable C component is a tested wire codec, not a complete flashed firmware
image. `link_capture_model.py` is a tested pre/post-trigger reference model, not
HDL, synthesis or hardware timing evidence. Board support, electrical protection,
front-end design, signed boot, drivers and physical commissioning remain separate.

## Start with an end-to-end example

Install this branch's ordinary NET package, then use a new output directory:

```sh
net link catalog
net link demo --profile GaugeLink --output-dir results/link-gauge-001
net link inspect results/link-gauge-001/investigation
```

The demo performs framing, chunked input, two SQLite stores, durable ACK/retry,
real Session execution, provider-free reopen, fresh-execution replay, native
export and HTML rendering. Open `results/link-gauge-001/dashplate.html`.
`demo-receipt.json` records actual identities and retry outcomes. All demo input
is explicitly authored synthetic data, including hardware/firmware declarations.
The display is a retained record, not a live instrument reading.

## One contract, seven application profiles

| Profile | Executable example | Separate application/physical work still required |
| --- | --- | --- |
| GaugeLink | Length readings with part/revision/lot/cavity/cycle/serial context | Exact caliper/force-gauge driver, fixtures, traceable comparison and real inspection |
| MachineLink | Pressure/temperature channels and declared manufacturing context | Machine signal conditioning, actual cycle binding, parent manufacturing consumer, commissioning |
| ConditionLink | Acceleration/frequency records | IEPE/ADC/filter characterization, continuous waveforms, tachometer/order analysis and diagnostic qualification |
| FieldLink | Three spatial scalar channels and explicit local reference context | Receiver/antenna/correction binding, spatial transforms, surveyed controls and GIS export qualification |
| ThermalLink | Temperature/humidity channels | Radiometric image format, camera/probe characterization, environmental correction and thermal procedures |
| DAQLink | Voltage recording plus common transport contract | ADC/range/isolation, hardware triggers, sustained-rate and clock qualification |
| CalLink | Reference-style length-record example and declaration review | Stimulus generation, actual references, uncertainty budget and calibration procedure execution |

These profiles do not load drivers or claim OEM compatibility. No capability
name, USB identifier, manifest, pixel UI or stored file can grant execution
permission. `control` is fixed to `NONE`.

## Implementation map for the supplied abstractions

| Area | Executable implementation | Boundary |
| --- | --- | --- |
| Identity | `link_contract.py` binds device, instrument, transducer/channel, hardware, firmware, optional gateware and configuration content | Declared identities and hashes do not attest installed hardware/software |
| Capability discovery | Strict manifest inspection against an externally bound exact manifest | No scanning, automatic registration, arbitrary driver import or CONTROL capability |
| Measurement schema | Strict raw/reported/processed-stage envelope with occurrence IDs, quality/missing status, procedure/context and native covariance | Bounded scalar/opaque ingress; not all scientific data formats |
| Raw preservation | Exact wire/payload/raw bytes retained with independent byte and content identities | Original bytes are not reconstructed from floating-point values |
| Clocks | Exact unsigned 64-bit ticks, boot, tick frequency, timestamped-event meaning and separate host receipt clock | No clock synchronization, timestamp calibration or invented acquisition time |
| Offline buffering | SQLite FULL/WAL transactions, capacity refusal, stable receipts, explicit local sync and retry | Local durable cache only; no remote receiver, cloud dependency or unattended scheduler |
| Transport | Python incremental decoder, finite file/stdin/chunk input, portable C framing and local cache-to-cache transfer | USB/serial/BLE/CAN/IP device access requires explicitly qualified transport adapters |
| Configuration | Content-bound expiring plans and host-authorized simulated apply with revision compare-and-set | Physical configuration is unavailable; configuration may itself have physical effects |
| Firmware/gateware | Real offline Ed25519 image/target/generation verification and local review counter | No flashing, installed-image attestation, secure boot or tamper-proof antirollback |
| Permissions | Separate host-bound READ/CONFIGURE/CALIBRATE/UPDATE/CONTROL evaluation | No user-account service, hardware trust root, key provisioning or automatic grants |
| Gateway/NET | Existing Session, OperationRegistry, payload validator, ObservationBus and native record schemas | No changes to core scientific identity, covariance or Workbench lifecycle |
| Dashplate | Responsive pixel-instrument HTML, row filtering, escaped raw/context details and explicit status | Read-only offline view; no public deployment or live/control endpoints |
| Capture hardware abstraction | Finite integer-tick, multichannel pre/post-trigger reference model | Not a claim of actual FPGA or simultaneous ADC acquisition |

## Wire protocol v1

Wire integers are unsigned, big-endian. There is no negotiation or fallback.

| Offset | Size | Meaning |
| --- | --- | --- |
| 0 | 4 | ASCII `NLK1` |
| 4 | 1 | Version `1` |
| 5 | 1 | Flags `0` unsigned, `1` HMAC |
| 6 | 2 | Kind `1` manifest, `2` observation, `3` event |
| 8 | 4 | Payload size, 1..65536 bytes |
| 12 | N | Original UTF-8 JSON bytes |
| 12+N | 4 | CRC32 of header and payload |
| 16+N | 0 or 32 | HMAC-SHA256 of header, payload and CRC |

The CRC uses the ordinary reflected CRC32 polynomial `0xedb88320`, initial and
final XOR `0xffffffff`. It detects corruption; it is not authentication. HMAC
keys are 32..64 raw bytes supplied by the trusted host, never selected by a
packet. HMAC establishes possession of that shared key, not physical device
identity, sample truth, permission or confidentiality. Encryption and mutual
remote authentication are not implemented. Do not expose this local ingress as
an Internet endpoint.

A decoder refuses unknown flags, version, kinds, impossible lengths, bad CRC,
bad MAC and incomplete endings. It becomes poisoned after an error; it never
skips arbitrary bytes to recover an apparently plausible observation. Valid
frames committed from earlier chunks remain retained when later input fails.
Live authenticated intake refuses unsigned downgrade. Offline inspection can
retain signed bytes without a key, but reports `not_reverified`; it cannot
renew a historical authentication result.

`examples/device-link/native/link_wire.c` independently implements unsigned
framing/CRC in allocation-free C11 with caller-owned buffers. The C codec
explicitly refuses MAC frames rather than pretending to verify them. It is
not a JSON parser, device driver, firmware update mechanism or reference for
physical measurements. The companion CLI is a host conformance tool.

## Manifest and observation semantics

The strict schema owners are `link_contract.manifest`, `validate_observation`,
`link_store.validate_event` and `link_gateway.validate_capture`. Unknown fields
are refused. `net link example` writes a complete manifest and finite wire file.

A channel declares exact quantity, unit, frame, transducer and encoding. The
initial decoded format is **Notation's own** `notation-ascii-scalar.v1`:

```text
24.982 mm\n
-1.250 V\r\n
```

It is one signed decimal, one ASCII space, the exact declared unit token and
LF or CRLF. This is not a guessed Mark-10, Mitutoyo, Modbus or GNSS protocol.
`opaque.v1` preserves uninterpreted bytes and an explicit missing numeric value.
No silent unit conversion, compensation, calibration fit or schema migration
occurs. A displayed instrument value is stage `reported`, not automatically
`calibrated`.

Processed stages require original parent observation content and an explicit
transformation reference. They are retained as declarations, not promoted to
native observations until a qualified transformation path is supplied. An
observation's occurrence identity binds device, boot and sequence. Original raw
bytes and full observation content have separate identities. Manufacturing and
spatial context are explicit optional fields, never invented genealogy or
survey authority.

## Timing and covariance

A native projection uses relative seconds from an explicitly retained origin
tick. The native clock identity binds device, manifest, boot, declared clock,
tick frequency and origin. Therefore different devices or selected origins do
not silently share a numerical time coordinate. The mapping retains the exact
integer numerator/denominator and original tick. Binary64 conversion is allowed
only when nearest-tick reconstruction yields the original relative tick;
otherwise the observation remains retained but its native projection is held.
Unknown ticks or tick frequency remain unavailable. Tick rollback within one
channel is refused, requiring an explicit new boot/capture.

`timestamp_event` distinguishes `sensor_sample`, `instrument_report_receipt`
and a declared event. Timestamping a received gauge message does not establish
when the gauge sampled. Host receipt timestamps are separate unsigned integers;
their clock relation remains `UNKNOWN`. An optional synchronization reference or
clock uncertainty is a declaration, not executed ClockSync or measured jitter.

Individual uncertainty uses the existing `covariance-artifact.v1` contract.
Joint uncertainty preserves the entire ordered matrix and input values, units,
frame and provenance. Cross-sample/cross-channel covariance is not diagonalized
into independent scalar uncertainties. Joint artifacts export separately with
all native rows pointing to their identity. Missing uncertainty remains missing.

Calibration declarations retain exact supplied certificate bytes, scope,
reference-standard reference and a half-open validity interval. Applicability
uses the observation's explicitly supplied acquisition time, not the current
wall clock. Missing acquisition time, unresolved references and mismatches
remain visible. Declared applicability is not certificate authentication,
metrological traceability, a newly executed calibration or release authority.

## Offline storage, recovery and permissions

Two caches have distinct UUID identities. An append transaction validates its
bound manifest, occurrence and exact bytes, commits, then reads back before
issuing a receipt. Exact retry returns the same receipt; changed bytes under
one occurrence refuse. A crash after receiver commit but before source ACK is
recovered by retransmitting the same bytes and recovering the receiver receipt.
The local sync API consults the actual receiver store, not a caller-authored
ACK document. Only recording bytes are retried, never physical actions.

A finite capture contains 1..256 frames and at least one observation, one
manifest and one boot/origin. Whole capture size is at most 4 MiB; raw observation
bytes at most 4096. Each local cache admits at most 4096 messages / 64 MiB of
wire bytes; state has its own bounded budget. Metadata/SQLite overhead is extra.
Full buffers refuse the new item without dropping old data or acknowledging it.
There is no automatic garbage collection or sensor-backpressure driver. Source
sequence gaps are explicit; a finite selected prefix is not proof that a device
finished its experiment. The host reference producer assumes a single producer
per boot; this is not an exactly-once physical acquisition guarantee.

Storage uses local regular files; explicit symlink checks and schema validation
refuse obvious substitutions. This is not a race-free sandbox against a hostile
filesystem or administrator. Power-loss durability depends on the filesystem
and storage honoring synchronization. Crash tests exercise actual process
termination and transaction recovery, not simulated power-loss hardware.

Host `Authority` objects are explicit runtime bindings, not authenticated user
accounts. Configuration plans bind exact old/new configurations and expiry.
Simulated apply advances a compare-and-set revision and starts a new boot,
leaving old observations unchanged and stale runtimes unable to append. The
retained recovery review includes the new manifest and plan. Reopening records
never restores live grants. All physical configuration, calibration, firmware
updates and CONTROL dispatch refuse.

Offline update review uses the optional cryptography dependency already declared
by NET. A trusted raw Ed25519 public key verifies a domain-separated manifest
and exact image. Target hardware/device and a monotonically increasing review
generation must match. Package data cannot supply the trust root. Review does
not install the image. The local review counter is neither secure boot nor
hardware-protected rollback state, and no private key is persisted.

## Explicit command journey

```sh
net link example --profile GaugeLink --output-dir link-input
net link record link-input/capture.link --manifest link-input/manifest.json --journal edge.sqlite --create
net link sync --source edge.sqlite --receiver gateway.sqlite --create-receiver --manifest link-input/manifest.json
net link capture --journal gateway.sqlite --manifest link-input/manifest.json --boot-id synthetic.boot.1 --output capture.json
net link run capture.json --output-dir investigation
net link inspect investigation
net link replay investigation --output-dir replay
net link export investigation --output-dir native
net link report investigation --output panel.html
```

Use new paths. `record -` explicitly selects finite standard input. HMAC intake
uses `--hmac-key KEYFILE --require-auth`. Neither file/stdin ingress nor local
sync opens a device or network connection. JSON output and exit 2 identify a
refusal; source declarations that reach Session retain actual refused execution.

`config-plan --manifest M --configuration C --expires-ns N --output P` prepares
only. `verify-update --manifest M --package P --image I --public-key K
--minimum-generation G` checks bytes/signature only. These CLI commands do not
apply configuration or flash a board.

## Integration and qualification

This increment is stacked on connectivity PR #202 at
`1517ef212be6f80092672d1ee6b05ad1df049bfb`, whose baseline was main
`0374076695475669188d61698e4656ced2263e01`. Connectivity is optional, separate
transport evidence. No Open5GS/free5GC core, radio, subscriber or network
configuration is changed.

PR #185 at `a7724105b5c3f59cabaef7085c9329bb7dfe1279` separately owns typed
Workbench data ingestion. Link exports its existing observation-stream and
covariance schemas with exact mapping references; no PR185 bridge is modified
and full parent-branch integration is not claimed. Native exported contexts
remain in the retained Link source and mapping, not silently copied into a new
manufacturing state owner. Parent integration must retain the original capture
and covariance ancestry rather than treating a display or scalar CSV as the
complete evidence package.

Only two existing code files receive a catalogue row: `operator_commands.py`
and `scientific_audit.py`. Session, Workbench, core identities, covariance,
numerical kernels, licensed components and provider pins remain unchanged.

```sh
python -m pytest -q tests/test_link.py tests/test_connectivity.py
python -m pip wheel --no-deps --no-build-isolation . --wheel-dir dist
python scripts/check_link_installation.py --require-pins --wheel dist/computational_instrumentation_workbench-0.1.0-py3-none-any.whl --output-dir link-installed
```

The installed gate requires the existing exact NumPy/websockets/pytest and
optional cryptography pins plus a C compiler. It copies explicit installed host
distributions to a noneditable isolated environment, verifies every package byte,
runs tests outside the source directory and refuses any skipped required case.
`--allow-host-dependencies` is an explicit non-pinned local qualification mode;
its receipt records version mismatches rather than changing repository pins.

Local execution on 2026-10-05: 186 Link cases plus 127 unchanged connectivity
cases passed in source and again in an isolated installed wheel, without skips.
All 423 source/wheel/installed package files matched. Seven installed profile
demos and existing catalogue audit passed. The 28 native C cases also passed
with AddressSanitizer/UndefinedBehaviorSanitizer; these are a subset of 186,
not additional distinct cases. Chromium tested filtering, details, no external
requests and no page-wide overflow at 1440x1000 and 390x844. Local file navigation
was blocked by the browser policy, so HTML bytes were rendered using set_content;
that is not a public-site deployment check.

Local environment: Linux Python 3.13.5, NumPy 2.3.5, websockets 16.0,
pytest 9.0.2, cryptography 46.0.4, GCC 14.2.0. Unchanged pins require NumPy
2.4.3 and cryptography 46.0.0. The strict pinned gate was executed and BLOCKED;
existing `net start` returned exit 2 at its unchanged NumPy preflight. No full
monorepo, hosted Windows, pinned-platform or physical qualification is claimed.
Complete package source was recovered from the retained CI wheel plus exact
current-main/PR202 additions; baseline package tree matched
`93f4c999efe599c3f3936676e8f8adc4b7ba6a84`. It is not a full monorepo checkout.
