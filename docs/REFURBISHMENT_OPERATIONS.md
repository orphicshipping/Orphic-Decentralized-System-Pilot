# Asset and refurbishment operations

NET now retains the operating record for intake → diagnosis → repair → configuration change → reassessment → review → release → customer service. A local SQLite registry keeps asset revisions, exact costs, parts, equipment configuration history, evidence and operator declarations together. `net refurb dashboard` exports a searchable offline desk; `net refurb report` writes customer or internal reports.

## Try the complete workflow

```sh
net refurb demo --output-dir results/refurb-demo-001
net refurb list --db results/refurb-demo-001/registry.sqlite3
net refurb show NS-DEMO-0001 --db results/refurb-demo-001/registry.sqlite3
net refurb verify NS-DEMO-0001 --db results/refurb-demo-001/registry.sqlite3
```

Open `results/refurb-demo-001/desk.html` in a browser. The demonstration retains a completed repair, updated configuration, passing assessment, explicit demonstration approval, customer record, warranty declaration and return. The return invalidates the current release. `customer-report.html` records the earlier release revision; `desk.html` shows the later returned state. Reports state their revision so an old customer report cannot be mistaken for the current registry.

The directory also contains editable commands, command receipts, exact-cost CSV exports, a portable archive, a restored registry, and a NET hardware assessment with a fresh replay. The restored registry reflects the archive's pre-return state. Its command history is replayed with new event identities; the source archive remains available as the original record.

All example data and decisions are visibly synthetic. Running this demonstration does not mean that equipment was received, repaired, erased, provisioned, approved by a human or physically qualified.

## Start an operational registry

```sh
net refurb init --db operations/assets.sqlite3
net refurb example --output examples/refurbishment-commands.json
```

The example is an array of command templates. Select one command into a separate JSON file, replace its identifiers and payload with the actual operator declaration, and review it before applying it:

```sh
net refurb apply intake-command.json --db operations/assets.sqlite3
net refurb show ASSET-0001 --db operations/assets.sqlite3
net refurb history ASSET-0001 --db operations/assets.sqlite3
```

Alternatively, keep just the action payload in a file:

```sh
net refurb action open_order --db operations/assets.sqlite3 --asset ASSET-0001 --revision 1 --actor "Operator name" --command-id "ASSET-0001:open-001" --payload open-order.json
```

Each command names its asset, expected revision, operator, unique retry identity, action and payload. Use the current revision from `show`. Repeating the **same** command identity and content returns the original receipt without applying it twice. Reusing that identity for different content or submitting a stale revision refuses. Reading an absent registry never creates one.

The registry is local and single-organization. Operator names are declarations, not authenticated identities. Restrict filesystem access to authorized operators. The hash-linked history detects accidental changes and inconsistent projections; it does not prove authorship against someone able to rewrite the entire database.

## Operating sequence

| Action | Retained information and gate |
| --- | --- |
| `intake` | Asset category, location, condition, configuration, declared source, acquisition cost and currency. Known serial identities cannot belong to two assets in the registry. |
| `open_order` | Work-order identity and scope; opening another order invalidates the current release. |
| `diagnose` | Findings, condition and referenced evidence. |
| `record_repair` | Work performed, replacement parts, quantities, unit costs, labor and other costs. A diagnosis is required first. |
| `replace_configuration` | A fresh configuration revision and reason. Prior configuration history remains retained. |
| `record_evidence` | Exact retained diagnostic/document/maintenance record and its content reference, source and configuration binding. |
| `reassess` | The full hardware request and all current evidence. NET recomputes the assessment; evidence must bind the exact observations or rederivable bench metrics. |
| `review` | Explicit approve/reject decision bound to the current assessment and configuration. Approval requires passing declared criteria. |
| `record_customer` | Customer identity, name, contact and notes. Updating a customer appends a new declaration. |
| `release` | Current review, equipment configuration, evidence, customer and disposition. Synthetic/public examples permit `demo_release` only. |
| `record_warranty` | Release-specific dates and supplied terms; NET does not invent a warranty policy. |
| `record_return` | Active release, received date, reason and condition. Clears the current release and review so a new work order can begin. |
| `relocate` | Updated location with a retained reason. |

The command templates and [contract](../src/ciw/refurbishment_contract.py) define exact fields and bounded types. Costs are **integer minor units**, with a currency and explicit decimal exponent fixed at intake. For CAD with exponent 2, `1250` means CAD 12.50. There is no automatic tax, exchange-rate, revenue-recognition or accounting-policy calculation.

After a repair, record a new `diagnose` declaration with the observed current condition before collecting fresh evidence. The demonstration explicitly records this post-repair condition; NET never invents an equipment grade from a numerical pass.

Repairs, configuration changes and new evidence invalidate prior assessment/review decisions. Evidence captured before a repair cannot be reused to approve the repaired state. Original decisions remain in history. There is no automatic equipment release or physical actuation.

## Bench evidence

Use [the bench guide](HARDWARE_BENCH.md) for read-only local inventory, bounded SMART capture, exact-byte SMART/diagnostic import, mappings into hardware requests, and data-only sanitization/provisioning plans. Public examples retain the upstream URL, commit, exact raw hash and license. Deterministic passing, failing, missing and stale cases are synthetic derivatives, not statistically representative equipment trials.

```sh
net bench example --case pass --output-dir results/bench-001
net bench verify results/bench-001/evidence.json --configuration results/bench-001/configuration.json
net hardware run results/bench-001/request.json --output-dir results/bench-assessment-001
```

Attach the full evidence record to the asset before reassessment; its asset/configuration/target identities must agree. Sanitization and provisioning plans contain explicit target bindings and pending prerequisites. Imported completion declarations remain statements from their source. NET does not erase disks, flash firmware, run stress tests or provision an operating system through these commands.

## Customer and accounting handoff

```sh
net refurb report ASSET-0001 --db operations/assets.sqlite3 --output customer/ASSET-0001-report.html
net refurb report ASSET-0001 --db operations/assets.sqlite3 --internal --output internal/ASSET-0001-report.html
net refurb dashboard --db operations/assets.sqlite3 --output internal/desk.html
net refurb export --db operations/assets.sqlite3 --format inventory-csv --output exchange/inventory.csv
net refurb export --db operations/assets.sqlite3 --format costs-csv --output exchange/repair-costs.csv
```

Customer reports omit internal contact data, raw diagnostics, internal diagnoses and repair costs. Internal reports and dashboards contain operational data and should be shared accordingly. HTML fields are escaped; exported spreadsheet formula-like cells receive a leading apostrophe. CSV money fields remain exact integers with currency/exponent columns.

CSV/JSON is the initial inventory/accounting connection. Import the exports using the destination system's reviewed mapping. NET does not silently update a remote ERP or claim that an export has been posted to the ledger.

## Archive and restore

```sh
net refurb export --db operations/assets.sqlite3 --output backups/assets-001.json
net refurb import backups/assets-001.json --db restored/assets.sqlite3
```

Exports validate the full registry in one read transaction. Restore verifies the archive content reference, event chains and replayed asset state, stages the complete new registry, then publishes it exclusively. A failed import never publishes a partial destination. New event occurrences are created; the import receipt retains the original archive hash and source registry identity. Keep the source archive and receipt together. Do not copy a live SQLite file as an alternative to this export.

All output files and directories are creation-only. Use a fresh name for each report or backup. Unknown database schemas, corrupt history, stale revisions, duplicate identities and mismatched evidence refuse rather than silently repairing history.

## Qualification boundary

`python scripts/check_refurbishment_installed.py --python INSTALLED_PYTHON --net INSTALLED_NET --wheel CANDIDATE_WHEEL --output-dir NEW_DIRECTORY_OUTSIDE_CHECKOUT` checks the installed package bytes and full software journey. CI runs one identical wheel on Linux/Windows and Python 3.11/3.12. It retains public/synthetic pass, fail, missing-data and stale-configuration outcomes, customer exports, retry/refusal behavior and restoration.

Physical-device acceptance and independent-human usability acceptance remain separate evidence categories. Public data can qualify parser and workflow behavior without establishing those claims. Consult [qualified release](QUALIFIED_RELEASE.md) for the exact candidate CI requirements.
