# Hardware characterization and refurbishment

The `net hardware` workflow connects one equipment configuration, imported
characterization records and an operator-defined numeric acceptance plan in a
retained NET investigation. It helps a technician record which configuration
was assessed and a customer inspect the declared checks and their results.

This first increment evaluates supplied records. It does not acquire hardware
measurements, erase disks, flash firmware, provision equipment or control a
physical device. Inventory, accounting, warranty decisions and release approval
remain outside this workflow. PASS means only that the supplied observations
satisfied the declared checks; it is not product certification.

## Run the synthetic example

Install the core package using the [operator quickstart](quickstart.md). No
optional scientific provider or attached equipment is needed for this example.
Choose new paths for each command that writes an artifact:

```text
net hardware example --output hardware-request.json
net hardware run hardware-request.json --output-dir results/hardware
net hardware inspect results/hardware
net hardware report results/hardware --output hardware-review.html
net hardware replay results/hardware --output-dir results/hardware-replay
```

The generated fixture is **synthetic**, including its equipment identity and
observations. It exercises the software workflow and supplies no evidence about
an actual machine. Open `hardware-review.html` to review the equipment,
configuration, declared source, individual checks and overall outcome.

`run` takes the request as a positional argument. Existing output files and run
directories are refused instead of overwritten. Keep the original directory
when replaying or creating a revised investigation.

| Command | Purpose |
| --- | --- |
| `net hardware example --output request.json` | Create the synthetic request for inspection and adaptation. |
| `net hardware configuration configuration.json --output identity.json` | Validate a standalone configuration and save its content identity; omit `--output` to print the identity only. |
| `net hardware run request.json --output-dir run` | Import a request, evaluate the declared checks and retain the investigation. |
| `net hardware inspect run` | Read and validate retained records without provider execution. |
| `net hardware replay run --output-dir replay` | Evaluate the retained input in a new execution and result occurrence. |
| `net hardware report run --output report.html` | Export the retained outcome as a customer-facing HTML report. |

## Prepare a real equipment record

Use `net hardware example` to obtain the complete supported request shape. Copy
it to a new file and replace the synthetic equipment, source, procedure and
observation declarations with records for the actual work. Do not relabel
synthetic measurements as captured measurements.

Give the physical asset a stable identifier and describe the exact tested
configuration, including the relevant components and versions. Prepare this
configuration as a standalone JSON object when calculating its content identity:

```text
net hardware configuration configuration.json --output identity.json
```

Set `observations.configuration_ref` to `configuration_ref` in `identity.json`
and `observations.asset_id` to `configuration.asset_id`. Matching these
references is a condition of evaluation;
it does not establish that the supplied description matches physical equipment.

Declare the source as `operator_import` for operator-supplied observations. This
records an import claim rather than authenticating acquisition. Retain the
actual bench records and measurement procedures as supporting operational
evidence. Select metrics, units and acceptance thresholds appropriate to the
particular product and workload; the example's limits are demonstration values.

## Request shape

The generated example is the complete runnable reference. Its JSON envelope has
`schema: "ciw.hardware-request.v1"` and exactly three content objects:

| Object | Required content |
| --- | --- |
| `configuration` | Schema `ciw.hardware-configuration.v1`; `asset_id`, `revision`, `manufacturer`, `model`, `serial_number`, `components` and `software`. Each component has `slot`, `kind`, `manufacturer`, `model`, `serial_number` and `firmware`; each software entry has `name` and `version`. |
| `plan` | Schema `ciw.hardware-plan.v1`; `plan_id`, `revision`, `purpose` and a nonempty `checks` list. |
| `observations` | Schema `ciw.hardware-observations.v1`; `asset_id`, `configuration_ref`, `source_kind`, `captured_at`, `operator`, `tool`, `environment` and `records`. `tool` has `name` and `version`; `captured_at` is an ISO timestamp with a timezone. |

Here are the matching synthetic check and observation fragments from the
example, for `plan.checks` and `observations.records` respectively:

```json
{
  "test_id": "memory-errors",
  "procedure_id": "example.memory-check.v1",
  "unit": "count",
  "minimum": 0,
  "maximum": 0,
  "minimum_duration_s": 600
}
```

```json
{
  "test_id": "memory-errors",
  "procedure_id": "example.memory-check.v1",
  "unit": "count",
  "value": 0,
  "duration_s": 600,
  "status": "completed",
  "notes": "Synthetic observation"
}
```

Bounds are inclusive; one bound may be `null`, but both cannot be absent.
Procedure and unit labels must match exactly; this workflow does not convert
units. Observation status is `completed`, `error` or `not_run`; the latter two
require a `null` value. Missing observations remain missing rather than becoming
zeroes. Duplicate test identities and observations for undeclared tests are
refused. Component slots and installed software names must also be unique.

A configuration contains 1–128 components and up to 128 software entries;
serial numbers and component firmware may be `null` when unknown. A plan has
1–64 checks, with up to 64 observation records. Include every declared field
and do not add unsupported fields. These are normalized summaries of supplied
bench records, not an importer for arbitrary diagnostic-tool output.

## Interpret results

| Outcome | Meaning | Exit code |
| --- | --- | --- |
| PASS | The supplied observations satisfy the declared acceptance checks. | `0` |
| FAIL | At least one declared check fails. | `2` |
| INDETERMINATE | An observation is missing, incomplete, uses a different procedure or unit, or has insufficient duration. | `3` |
| Refusal | The input or retained binding is invalid or inconsistent. | `1` |

An out-of-bounds completed value fails its check even if its duration is short.
Any failed check makes the overall result FAIL; otherwise an indeterminate
check makes it INDETERMINATE. PASS requires every check to pass.

Creation-only commands return `0` on success. Outcome-bearing commands return
the outcome code. `report` still writes the HTML for a FAIL or INDETERMINATE
outcome and then returns its outcome code; automation must distinguish these
results from a refusal. Review individual check findings and declared limitations
before using any result in a sale, installation or service decision.

## Preserve configurations and evidence

The run retains the exact input bytes as `request.json`, with their digest
bound in its source record. Existing NET Session identities distinguish the
input evidence from the operation, execution and result. Reopening or reporting
reads retained state without silently executing a provider. Explicit replay
creates fresh execution and result identities for the same retained input; it
does not perform a fresh measurement on the equipment.

The dedicated hardware bundle uses its generated workspace-v2 shape. Hardware
inspection and export refuse manually extended or corrected workspaces rather
than ignoring added correction journals or native artifacts. Prepare a new
declared request and a new run when revising evidence.

After replacing a component, updating firmware or otherwise changing a tested
configuration:

1. Keep the old configuration, observations and run intact.
2. Author a new configuration revision and calculate its identity.
3. Capture observations for that revision and bind them to its new identity.
4. Run a new request into a new directory and review the new outcome.

Do not change a configuration reference on old measurements to make them appear
current. A repaired machine's previous failure remains part of its engineering
history; the new result records what was assessed after the repair. Changing an
acceptance plan likewise creates a new investigation rather than rewriting the
old result.

The HTML is a readable projection of retained records. Source declarations,
checks and limitations must travel with any customer-facing interpretation.
Content bindings detect inconsistencies; they do not authenticate an operator,
prove physical truth, establish calibration or authorize release for sale.

## Verify an installed wheel

The repository's hardware CI runs the hardware and shared-substrate tests on
Windows and Ubuntu with Python 3.11 and 3.12. Its installed-wheel check exercises
the console commands and isolated Python inspection from a directory outside
the source checkout. To check a wheel already installed in a separate environment:

```text
python -I scripts/check_hardware_installed.py --python /path/to/environment/python --net /path/to/environment/net --wheel /path/to/package.whl --output-dir /new/path/outside/checkout/hardware-check
```

On Windows, use that environment's `Scripts/python.exe` and `Scripts/net.exe`.
The environment and output directory must both be outside the source checkout;
the output directory must not exist. The check compares installed package bytes
with the supplied wheel, retains command receipts in `commands/`, and writes
`qualification.json`. It checks configuration binding, original request bytes,
read-only inspection, fresh replay identities, report labels, overwrite
refusals, and FAIL/INDETERMINATE exit codes. All equipment and observations in
this gate are synthetic; software qualification does not establish physical
performance or release authority.
