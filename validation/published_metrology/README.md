<!-- ncp-folder-entry -->
# MCP entry

- path: `validation/published_metrology`
- class: `evidence`
- authorizes_execution: false
- call: `none`

This folder does not opt for the easiest task in a set. Name the task.

This folder is not a menu. It still does not select a task for the caller.

The contract is [MCP.md](MCP.md). A file in this folder is not a tool call. This folder is not a human console. Reading this page does not run a solver, bind a provider, or qualify a result.

---

# Published metrology kernel benchmark

This opt-in harness reproduces 147 bounded reductions from published KRISS and
BIPM data using a separately built, installed NET wheel:

- 136 static thermocouple affine reductions from author-cached workbook values;
- 6 guarded-hot-plate root-sum-square uncertainty reductions;
- 5 helium-leak root-sum-square uncertainty reductions from printed Table 12.

The current retained run passed all 147 cases on source revision
`1f086a30d13193f25847f329e98571479b287369`.
Twenty integrity tests also passed. See [validation-summary.json](validation-summary.json)
for the exact wheel digest, module digests, environment, execution scope and the
separate historical run identity. This is local test evidence; it does not assert
that remote CI executed the benchmark.

## External inputs

This repository publishes the harness, metadata and result summary. It does not
redistribute the XLSX, PDF, derived fixture values or historical wheel. A checkout
alone is insufficient to run this benchmark.

Provide an external directory containing the four exact files in
[external-inputs.json](external-inputs.json), with the listed relative paths.
An existing `NET-metrology-corpus-0.2` directory has these files. The fifth
required input is `qualification/inputs.json`; its exact historical-run digest
and byte count are also listed in the input contract. This manifest must list
the four source/fixture files with their
SHA256 digests and byte sizes. The runner also accepts the historical wheel entry
from corpus 0.2, but ignores that entry: the current tested wheel is always
provided and verified separately. The old wheel does not need to be present.

The two JSON fixtures are NET-derived corpus artifacts; they are not downloads
offered by the primary publishers. Operators without those pinned fixtures need
to provision the corpus artifacts before running. There is no acquisition step,
implicit network request, synthetic substitute or optional-data pass.

The original source URLs and attribution are in
[external-inputs.json](external-inputs.json):

- Inseok Yang and Daeho Kim, Korea Research Institute of Standards and Science,
  [Dataset for the high temperature GHP uncertainty paper, version 2](https://data.mendeley.com/datasets/4gw5r25wtz/2),
  DOI 10.17632/4gw5r25wtz.2, published 17 August 2022; CC BY 4.0.
- K. Jousten and coauthors, BIPM / CCM and participating national metrology
  institutes, [Final report of key comparison CCM.P-K12 for very low helium flow rates](https://www.bipm.org/kcdb/comparison/download/published-section-block-file/16/46/statement/46/ccm.p-k12.pdf),
  DOI 10.1088/0026-1394/50/1A/07001.
  [BIPM reuse terms](https://www.bipm.org/en/copyright) require source attribution
  and no implied endorsement, subject to any material-specific terms.

## Reproduce with an installed wheel

The runner pins both numerical module files to the bytes in revision
`1f086a30d13193f25847f329e98571479b287369`.
Build from that source revision, or from this benchmark branch whose NET package
is unchanged. Build timestamps can change the whole-wheel digest; record the
digest of the wheel actually installed and pass it explicitly. A different
module digest requires review and an intentional harness update.

Example PowerShell commands from the repository, with an existing Python build
environment and the external corpus path supplied by the operator:

```powershell
python -m build --wheel --outdir ..\metrology-dist
python -m venv ..\metrology-runtime
$wheel = (Resolve-Path ..\metrology-dist\computational_instrumentation_workbench-0.1.0-py3-none-any.whl).Path
& ..\metrology-runtime\Scripts\python.exe -m pip install $wheel
$sha = (Get-FileHash -Algorithm SHA256 -LiteralPath $wheel).Hash.ToLowerInvariant()
$corpus = (Resolve-Path 'PATH_TO_EXTERNAL_CORPUS').Path
& ..\metrology-runtime\Scripts\python.exe -I validation\published_metrology\qualify_published.py --root $corpus --wheel $wheel --wheel-sha256 $sha --output ..\metrology-report.json

$env:NET_QUALIFICATION_CORPUS = $corpus
$env:NET_QUALIFICATION_WHEEL = $wheel
$env:NET_QUALIFICATION_WHEEL_SHA256 = $sha
& ..\metrology-runtime\Scripts\python.exe -I -m unittest discover -s validation\published_metrology -p test_qualify_published.py -v
```

Use a new output report path. Existing reports are never replaced. The standard
Python build module must be installed in the build environment; NET runtime
dependencies come from the wheel metadata. The tests use only the standard
library in addition to that installed package.

Before numerical execution, the runner checks source and fixture bytes,
manifest completeness, the explicit wheel digest, every installed CIW package
file against that wheel, import origins and the two reviewed module digests.
The tests reject changed sources, edited/resealed fixtures, missing inputs,
nonfinite values, wrong wheel digests and a resealed wheel that differs from the
installed package. Temporary integrity-test copies are removed after use.

## Meaning of a passing run

Expected values are author-cached workbook reductions and manually transcribed
printed values. Tolerances express numerical reproduction or printed precision;
they are not instrument accuracy or measurement uncertainty allowances.

The affine check calls the existing private `_sensor_output` kernel with
harness-only time and zero-covariance placeholders. It checks nominal
temperature and discards the placeholder uncertainty result. The RSS checks
reproduce the published diagonal-covariance calculation; they do not establish
physical independence of uncertainty components.

Passing does not qualify full acquisition, calibration certificate chains, MCP
admission, retained execution, result-to-ledger delivery, correlated experimental
uncertainty, the NET thermal inverse model, a full leakage workflow, or held-out
physical accuracy. Reference snapshots are immutable and are not continuously
monitored for publisher corrections.

## Output and runtime troubleshooting

The CLI reserves a new report before reading the corpus or executing numerical
cases. It creates missing parent directories, refuses existing reports without
rerunning the benchmark, and reports unwritable destinations as `blocked_output`
JSON with a suggested next step. Input/runtime failures are retained in the new
report with their exception type and message. stdout always includes the report
path when a report was saved.

Wheel validation compares the complete installed package inventory with the
candidate archive, then checks every file's contents and the imported module
origins. Omitting a package file or duplicating an archive member is refused,
even if the supplied wheel SHA256 matches that altered archive.

Ctrl+C retains an `interrupted` JSON report and exits 130. This report explicitly
states that no complete qualification result was retained; it contains no
passing case count. Start a new report path for a subsequent attempt.
