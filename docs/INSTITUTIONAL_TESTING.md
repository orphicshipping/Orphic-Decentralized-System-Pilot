# Institutional data campaign

This campaign runs existing NIST, Oxford, CWRU and EPA source files through NET
numerical and retained-data workflows. It requires an installed candidate wheel,
checks every installed `ciw/` file against that wheel before and after execution,
and launches each lane in an isolated Python process outside the checkout.
It does not acquire new data or establish physical qualification.

## Prepare the runtime

Build the candidate wheel, then install its optional `institutional-tests` extra
in a separate virtual environment. Use absolute paths and a runtime outside the
checkout. The extra pins SciPy 1.17.1, MCP 1.26.0 and tzdata 2026.5; NET also pins
NumPy 2.4.3 and websockets 16.0. MCP being installed does not imply an MCP
end-to-end test was performed.

```powershell
python -m pip wheel --no-deps --wheel-dir <wheel-directory> <checkout>
python -m venv <runtime-directory>
<runtime-python> -m pip install '<absolute-candidate-wheel-path>[institutional-tests]'
```

Alternatively, install with uv into the same separate virtual environment:

```powershell
uv pip install --python <runtime-python> '<absolute-candidate-wheel-path>[institutional-tests]'
```

The installed wheel must be the exact local file subsequently supplied with
`--wheel`. Both pip and uv are supported. The gate requires the exact installation
URL and compares every installed `ciw/` file with the wheel bytes. An installer
SHA256 is checked when supplied; missing hash metadata is permitted because the
wheel SHA256 is computed and the installed bytes are independently compared.
The report records this hash provenance and the optional installer hash.
An editable/source installation is refused. The campaign itself and
its three harnesses remain in the selected checkout; all NET imports come from
the installed package. The CWRU lane separately identifies and hashes its STFE
provider source from the bundled instrument tree.

## Supply original data

Use already acquired originals. Each harness checks retained or pinned SHA256
values before processing data.

| Argument | Required contents |
| --- | --- |
| `--corpus-root` | Corpus `catalog.json`, original EPA assets referenced by it, and `assets/foundations/Lew.dat` plus `NumAcc4.dat`. |
| `--thermal-dir` | Oxford author's `Temperature_data.mat`, `Voltage_current_data.mat`, `Impedance_data.mat`, `MainScript.m`, `README.md` and `LICENSE.txt`, at revision `194919b94b45f66309475bc88aa8e8826eb603bb`. |
| `--cwru-dir` | The original files named `CWRU-97.mat` and `CWRU-105.mat`, matching the academic harness hashes. |

NIST Lew contains observed beam deflections. NumAcc4 is an additional constructed
numerical stress benchmark and remains separate from real-observation counts.
Oxford includes published core/surface/coolant temperature recordings. CWRU
amplitude units and calibration remain unverified; EPA QA fields and missing
pairs remain explicit. Source URLs, transformations and limits are retained by
the individual lane reports.

## Run

Choose fresh output and scratch paths, both outside the checkout. The output
root is create-only: an existing directory causes a refusal. Use another new
path for each run. The academic scratch directory holds bounded raw-amplitude
receipts and must stay outside distributable output archives. If `--work-dir` is
omitted, a fresh location in system temporary storage is retained for diagnosis.

```powershell
Set-Location <working-directory-outside-checkout>
<runtime-python> -I -X utf8 <checkout>/scripts/run_institutional_campaign.py `
  --wheel <absolute-candidate-wheel-path> `
  --corpus-root <original-corpus-directory> `
  --thermal-dir <original-Oxford-directory> `
  --cwru-dir <original-CWRU-directory> `
  --output-dir <new-campaign-output-directory> `
  --work-dir <new-academic-scratch-directory>
```

Optional `--rwth-response <existing-file>` forwards an already acquired response
for archive-format diagnosis. It performs no acquisition. For an exported STFE
provider tree, pass `--stfe-source-root <directory>` and its retained
`--stfe-revision <revision>`; the academic report records provider file digests.

Each of the three lanes has a 600-second process timeout. A failed lane does not
stop the other independent lanes. `campaign.json` records before/after wheel
binding, timestamps, duration, commands, per-lane summaries and log/report hashes.
Each lane also retains `process.log`, `outcome.json`, and its `results/` directory.
Any failed/timed-out lane or failed wheel-binding check makes the campaign exit
nonzero. Successful software checks can coexist with expected refusals,
INDETERMINATE results and EPA diagnostic disagreements; these are not silently
promoted to physical acceptance.

This campaign covers the explicitly reported numerical kernels, native data
contracts and local CLI/provider processing. It does not assert calibrated
thermal-estimator qualification, hardware performance, State Ledger admission,
or end-to-end MCP operation.

## Follow progress and troubleshoot

The command reports each lane starting and finishing on stderr, including its
retained process-log path. stdout contains one final JSON object for automation.
Use `--quiet` to suppress progress while keeping that JSON summary. A nonzero
exit includes the failed lane names, failure reasons and log paths, or the
preflight/wheel-binding error. Independent lanes still run after a lane fails.

If the command refuses an existing output directory, choose a new output path;
previous evidence is never overwritten. If a lane fails, inspect the indicated
`process.log` and `outcome.json`, correct the reported input/runtime problem, and
start a new campaign. Preserve the failed run alongside its replacement.

Pressing Ctrl+C stops further lanes, retains the interrupted lane's partial log
and outcome, and writes the final campaign report with `INTERRUPTED` status. The
command exits 130. Counts include an interrupted executed lane among failures;
later lanes are counted as not run. An interrupted run never qualifies the
campaign. Choose a new output path to rerun after reviewing retained evidence.
