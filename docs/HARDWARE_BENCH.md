# Hardware bench acquisition and planning

`net bench` connects retained bench evidence to the existing `hardware.assess.v1`
comparison. Read-only acquisition, imported declarations and synthetic examples
remain distinguishable. It does not execute sanitization, firmware changes,
provisioning, stress tests or release authorization.

## Start with the offline example

```sh
net bench example --case pass --output-dir bench-demo
net bench verify bench-demo/evidence.json --configuration bench-demo/configuration.json
net hardware run bench-demo/request.json --output-dir bench-assessment
net hardware report bench-assessment --output bench-report.html
```

`--case fail` creates a failing SMART/media-error example. `missing` removes a
required metric and yields `INDETERMINATE`. `stale` changes the configuration
revision after the evidence was bound and must be refused. `public` preserves
the upstream fixture bytes. Every example assessment is `synthetic`; none can
establish the condition of a customer's machine.

The installed package includes an unmodified 1,735-byte
[upstream smartctl_exporter test fixture](https://github.com/prometheus-community/smartctl_exporter/blob/6274decc61212cc61e58796cc3b8cf5cfa035809/testdata/INTEL_SSDPE2KX080T8_1.json)
at commit `6274decc61212cc61e58796cc3b8cf5cfa035809`, its
[Apache-2.0 license](https://github.com/prometheus-community/smartctl_exporter/blob/6274decc61212cc61e58796cc3b8cf5cfa035809/LICENSE),
and `bench_examples/attribution.json`. Its SHA256 is
`2fd5253bb0792ac6472db1add36feb757218b8d42dee40d60752cfa73d6b68cf`.
The upstream serial/firmware identifiers are already redacted. This is public
software test data, not an authenticated physical acquisition. Derived examples
record the parent source, hash and transformation. Their counters and thresholds
are demonstrations, not statistical calibration, remaining-life estimates or
validated refurbishment acceptance limits. No network access or purchase is
needed to run them.

## Read-only inventory

```sh
net bench inventory --asset-id NS-0001 --operator "Bench operator" --output local-inventory.json
```

Windows uses fixed CIM queries through the system PowerShell installation with
profiles and module autoload disabled; its required modules are loaded from the
system module directory. Linux uses a fixed `lsblk` invocation from root-owned,
non-writable system locations plus bounded local `/proc` and DMI reads. Neither
collector resolves tools from `PATH` or executes commands from saved input.
Output is capped at 32 KiB, subprocesses at 20 seconds, and Windows categories
at 64 rows. Limits, missing tools, unavailable fields and query errors remain
explicit. Null measurements are never changed into successful zeroes.

The asset association is caller-declared. Inventory describes what the local
operating system reports; it does not prove that an asset label identifies that
host. Serial identifiers and raw output stay in the requested local file. The
console prints only a summary. Keep inventory and customer evidence out of
public reports or source control unless deliberately redacted.

## Import diagnostic evidence

```sh
net bench import smart.json --configuration configuration.json --slot storage-0 --operator "Bench operator" --captured-at 2026-10-04T12:00:00Z --procedure-id net.smart-snapshot.v1 --output evidence.json
net bench request --configuration configuration.json --plan acceptance-plan.json --evidence evidence.json --mappings mappings.json --output request.json
net hardware run request.json --output-dir assessment
```

The configured component model and serial must exactly match the imported
record. Operator imports require a known serial, not `REDACTED`, `UNKNOWN` or a
NET synthetic identifier. The evidence retains the exact raw bytes, their
SHA256, tool version, declared procedure/time, target and complete configuration
reference. Revalidation reparses those bytes and recomputes the result; merely
resealing an edited metric cannot make it valid.

Mappings are an array such as:

```json
[{"test_id":"media-errors","evidence_ref":"sha256:<evidence record digest>","metric_id":"nvme-media-errors"}]
```

Supported SMART metrics are overall health (0/1), current temperature, power-on
hours, power cycles and the NVMe media-error, critical-warning, spare and
percentage-used fields. Missing metrics are unavailable. No vendor-specific ATA
raw counter or inferred health percentage is invented. The low three smartctl
exit-status bits indicate acquisition errors; health/error-history bits remain
valid reported measurements rather than being discarded as command failures.
See the [smartctl manual](https://github.com/smartmontools/smartmontools/blob/main/src/smartctl.8.in).
A SMART snapshot always has zero sustained-test duration.

`--format diagnostic-json` supports bounded operator-supplied test results:

```json
{
  "schema":"ciw.bench-diagnostic.v1",
  "device":{"model":"Exact configured model","serial_number":"Exact configured serial"},
  "tool":{"name":"Operator's diagnostic tool","version":"1.0"},
  "procedure_id":"shop.memory-test.v1",
  "duration_s":600,
  "metrics":[{"metric_id":"memory-errors","unit":"count","value":0,"status":"available"}]
}
```

Unavailable/error metrics require null values. The declared duration and
procedure must match the raw diagnostic JSON. The assessment mapper preserves
the metric, unit, procedure, duration and raw/evidence references. It does not
translate units, assess evidence age, authenticate the operator or calibrate the
test. A changed configuration invalidates the binding.

## Explicit-device SMART capture

```sh
net bench smart --configuration configuration.json --slot storage-0 --device /dev/sda --operator "Bench operator" --output capture.json --evidence-output evidence.json
```

This uses only JSON, identity, health and attribute reads with a standby check.
It does not enable SMART, start self-tests, wake a sleeping disk intentionally or
write device state. A standard installed `smartctl` is required; NET does not
install tools or elevate privileges. Supported paths are simple whole-device
SATA/NVMe paths and Windows `/dev/pdN` paths. RAID/controller helper executors,
arbitrary paths and extra flags are refused. The capture retains executable
identity and failures; successful diagnostic evidence can be written separately
for assessment. An unavailable tool returns exit code 3 and retains an
unavailable capture, without creating a fabricated evidence record.

## Data-only maintenance plans

```sh
net bench plan --configuration before.json --expected-configuration after.json --slot storage-0 --action sanitize --procedure-id shop.sanitize-review.v1 --operator "Bench operator" --output maintenance-plan.json
net bench verify maintenance-plan.json
```

`sanitize` and `provision` plans bind the original and expected configurations,
asset, component model/serial, procedure and operator declaration. Both carry a
destructive-operation flag and pending ownership, identity, retention, backup
and recovery preconditions. They contain no executable or shell command, and
`execution` is always `not_supported`. `--source-kind synthetic` creates a demo
plan without promoting it to an operator plan.

`net bench import-maintenance completion.json --plan maintenance-plan.json
--output maintenance-evidence.json` checks an externally supplied
`ciw.hardware-maintenance-result.v1` declaration. The record must contain the
plan reference, exact target, before/expected configuration references, action,
procedure, operator, timezone-aware performance timestamp, tool name/version,
status (`completed`, `failed` or `not_run`) and verification declaration
(`declared_pass`, `declared_fail` or `not_performed`, with an evidence reference
for a declared verdict). `net bench verify maintenance-evidence.json --plan
maintenance-plan.json` rechecks the retained bytes and bindings.

A consistent imported completion declaration does not establish that erasure or
provisioning occurred, that verification is independent, or that pending
preconditions were authorized. `hardware.assess.v1` remains a declared-limit
comparison. Physical qualification, calibration, authenticated acquisition and
release decisions require separately retained operator/process evidence.
