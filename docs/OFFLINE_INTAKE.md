# Offline observation intake

`ciw.offline_intake.OfflineIntakeHost` interprets a bounded, host-selected JSON
capture of observations. This is an ODSP export format, not a ROS bag/CDR, OPC UA
wire, or MQTT packet decoder. It opens no socket, broker, device, file, retained
store, command channel or subscription. No intake result grants execution, state
admission or control authority.

The supported records are ROS joint-state exports, OPC UA scalar DataValue
exports, and MQTT scalar-observation exports. Source identities, clock identities,
units, timestamps, quality and delivery flags remain unverified source or host
assertions. A byte pin establishes which capture was read, not who produced it or
whether a sensor or simulation is physically valid. `origin` distinguishes a
synthetic fixture from an operator-declared capture; it authenticates neither.

## Operations and host binding

| Tool | Exact caller arguments | Result |
| --- | --- | --- |
| `ncp_intake_describe` | `{}` | Formats, limits and host capture provenance |
| `ncp_intake_records` | Optional `offset`, `limit`, `protocol` | Record metadata, complete offset paging |
| `ncp_intake_record` | `record_id` | Complete original record, interpretation and receipt |
| `ncp_intake_verify` | `record_id`, `receipt_json` | Full-content comparison with fresh interpretation |

All argument objects are closed. Paging defaults to 16 records and permits 1–32;
an offset beyond the selected records refuses. All four tools are read-only,
non-destructive and idempotent. No resources or resource templates are exposed.
Unknown operations and attempted retention, handoff, publication, subscription,
write, owner, endpoint, credential or path arguments refuse.

Only the trusted launcher supplies `capture_bytes`, `expected_digest`,
`source_identity`, `principal` and `origin`. The capture must match all these
bindings exactly. The principal facade additionally checks current tool grants
and the selected host's `_bound_principal`; a direct Python host is
not remote authentication. Native-profile family is `intake`. The integration
owner maintains shared service/policy routing.

The launcher reads an approved file with its own custody rules and obtains the
expected digest from its approved capture manifest. Caller arguments cannot
select that file or change the binding. Representative trusted-host code:

```python
import asyncio
from ciw.offline_intake import OfflineIntakeHost, serve_stdio
from ciw.services import invoke

# All five values below are trusted launcher inputs, not tool-call arguments.
host = OfflineIntakeHost(capture_bytes=approved_bytes,
                        expected_digest=approved_capture_digest,
                        source_identity=approved_source_identity,
                        principal=selected_principal,
                        origin=declared_origin)
description = invoke("ncp_intake_describe", {}, host=host)
# Use the existing principal facade around host for an authenticated boundary.
# serve_stdio accepts that facade as well as a locally bound host.
asyncio.run(serve_stdio(host))
```

The optional stdio service uses the pinned official MCP SDK 2.3.0 and existing
strict raw-input framing/descriptor custody. Malformed frames, including duplicate
keys, terminate the session before SDK decoding can erase information. There is
no compatibility fallback to a different SDK or unbounded input reader. Stdio
transport does not supply identity authentication or a network sandbox.

## Exact capture contract

Every object below rejects additional fields. Root fields are:

```json
{
  "schema": "odsp.offline-intake.capture.v1",
  "principal": "operator-a",
  "source": {
    "source_id": "reviewed-export",
    "revision": "fixture-v1",
    "digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  },
  "origin": "synthetic",
  "captured_at": "2026-11-03T01:02:03.123456789Z",
  "records": []
}
```

The shown digest is a synthetic example, not a source receipt. `source` must match
the host declaration; this module does not retrieve or authenticate that source.
`origin` is exactly `synthetic` or `operator_capture`. Each record contains exactly
`record_id`, `protocol`, `received_at` and `observation`. Record IDs are unique,
1–128 ASCII characters, start with an alphanumeric, then contain only
alphanumerics or `._:/-`. The same grammar bounds source/revision/clock/joint IDs.
Path-looking IDs are inert lookup keys; this profile never interprets them as
filesystem paths. Principal binding uses the existing ASCII principal spelling
with `_.:@-`, maximum 128 characters.

UTC envelope/export timestamps use `YYYY-MM-DDTHH:MM:SS[.1–9 digits]Z`. Calendar
and clock fields are validated; leap seconds and timezone offsets refuse. Exact
fractional text is retained. No clock conversion, synchronization, ordering or
freshness claim is made.

### `ros2.joint-state.v1`

`observation` contains exactly:

| Field | Contract |
| --- | --- |
| `stamp` | `{sec, nanosec}`; signed int32 seconds and integer nanoseconds 0–999,999,999 |
| `clock` | `{domain, clock_id}`; domain `simulation` or `source`, explicit opaque clock ID |
| `frame_id` | Valid Unicode string, 0–128 characters |
| `name` | 1–64 unique opaque joint IDs |
| `joint_type` | Same length, each `prismatic` or `revolute`, declared by exporter |
| `position`, `velocity`, `effort` | Each empty or same length as `name`; finite numbers |

Missing arrays remain missing values, not zeros. Prismatic values are labeled
`m`, `m/s`, `N`; revolute values `rad`, `rad/s`, `N.m`, based on the exporter-declared
joint type. The original arrays and exact clock stamp remain in the receipt.
This narrow convention follows the published
[ROS JointState field semantics](https://docs.ros.org/en/jazzy/p/sensor_msgs/msg/JointState.html);
it does not establish ROS transport compatibility, joint travel limits, actuator
safety or the correctness of declared units.

### `opcua.scalar.v1`

`observation` contains exactly `node_id`, `datatype`, `value`, `unit`,
`status_code`, `source_timestamp`, `server_timestamp`. `node_id` is an opaque
nonempty string up to 256 characters without ASCII control characters; it is not
parsed as an OPC UA address. `unit` is a string up to 32 characters.

Supported types are `Boolean` (exact Boolean), `Int32`, `UInt32`, `Double` (bounded
finite JSON number) and `String` (up to 256 Unicode characters). Arrays, int64/uint64,
extension objects and binary encodings refuse. `status_code` is an exact uint32;
the two high bits label source-reported good, uncertain, bad or reserved status.
`value: null` is allowed only for a non-good status. A null, bad or reserved value
is not presented as an available observation. The full reported value and status
are still preserved in the original record. Each timestamp is a strict UTC string
or `null`; missing source time stays explicit. This preserves the distinction in
the [OPC UA DataValue contract](https://reference.opcfoundation.org/specs/OPC-10000-4/7.11)
between a value and its source status; the reported quality is not independently
verified.

### `mqtt.scalar-observation.v1`

`observation` contains exactly `message_type`, `topic`, `qos`, `retain`, `duplicate`
and `payload_b64`. `message_type` must be `observation`. The exact topic is
preserved, including spaces and leading/trailing slashes; it is 1–256 UTF-8 bytes,
contains no NUL or `+`/`#` wildcards. These are a bounded subset of the
[MQTT topic-name rules](https://docs.oasis-open.org/mqtt/mqtt/v5.0/mqtt-v5.0.html),
not broker/topic authorization. QoS is exact integer 0–2; both flags are exact
Booleans and remain unverified delivery claims.

The payload is canonical base64 of at most 4,096 raw UTF-8 JSON bytes. Its decoded
object contains exactly:

```json
{"schema":"odsp.scalar-observation.v1","value":21.5,"unit":"degC","observed_at":"2026-11-03T01:02:03Z"}
```

`value` is a bounded finite number, `unit` at most 32 Unicode characters, and time follows
the UTC contract above. The original encoded bytes and their SHA-256 are retained
in the receipt. Arbitrary payloads, command/reconfiguration schemas, binary MQTT
messages and embedded instructions are not admitted. Inert labels are not
interpreted as executable instructions, even if their text resembles one.

## Bounds, receipts and verification

Capture input is at most 262,144 bytes, 256 records, depth 16, and 16,384 JSON nodes.
Request data is at most 65,536 bytes. Every complete response, including the
duplicated MCP text/structured envelope, is at most 262,144 serialized bytes.
Individual receipts must fit a 32,768-character `receipt_json` and the complete
verify request budget. Host construction refuses records that cannot round-trip
through both read and full receipt verification, even if a per-field maximum
would otherwise allow them. These limits also apply to Unicode escaping.

The shared `campaign_contracts` parser rejects duplicate keys before information
loss, invalid UTF-8, lone surrogates, excess depth/nodes/array/object/string sizes,
nonfinite numbers, overflow, nonzero float underflow, and integers outside
±(2^53−1). Field-specific integer checks reject Boolean-as-integer. The scalar
magnitude bound is 1e150. Python sorted compact ASCII JSON defines canonical
receipt bytes; it is not JCS or upstream attestation.

The record receipt contains its complete original record, fresh interpretation,
request, capture/source/principal provenance, all false authority claims, and a
digest over every other field. Verification reparses the submitted raw
`receipt_json`, recomputes the entire expected receipt from the bound capture,
then compares full canonical content. Forged fields refuse with either the
original digest or a newly calculated digest. Duplicate receipt keys refuse.
Verification does not authenticate an external producer or admit state.

## Local verification and remaining gates

`tests/test_offline_intake.py` uses explicitly synthetic records. It exercises
the interpretations, missing/bad data, refusal matrices, changed receipts,
deterministic mutation cases, exact host binding, pagination and envelope limits.
The official external MCP client starts separate server processes for discovery,
read/verify/denial/reconnect checks; a raw duplicate-key frame is separately tested.
`tests/test_offline_intake_policy.py` proves current SDK and external MCP grants,
record constraints and revocation refuse before the observation host is called.

From a source checkout with the pinned project test environment:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -B -m unittest discover -s tests -p 'test_offline_intake*.py' -v
```

This gate is local capture, policy and transport validation. Installed-wheel import
and release checks belong to the combined candidate. No
live ROS/DDS, OPC UA endpoint, MQTT broker, deployed identity, device behavior,
physical accuracy, industrial safety or production readiness is established.
