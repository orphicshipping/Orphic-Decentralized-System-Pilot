# Timed provider-free domain examples

`check_domain_examples.py` exercises nine small examples through the installed public NET commands and Python APIs. It requires a clean wheel installation, isolated Python, a working directory outside the checkout, and a new output directory. Any missing dependency, timeout, refused example, failed numerical condition, or failed replay makes the command exit nonzero. There are no provider skips.

These are bounded software examples. Passing them does not establish physical accuracy, authentic calibration, hardware acquisition, state admission, formal proof, or the availability of an external runtime.

| Example | Executed path and retained result | Condition checked |
| --- | --- | --- |
| Oscillator | `ciw.instruments.make_demo_run`, statistics and spectrum | 768 analytic synthetic samples; initial state, decaying energy and scalar-reference mean |
| Elastic impact | `net impact example/run/verify/export` | Default bounded elastic-contact trajectory and fresh independent numerical audit are `LOCAL` |
| Fluid reservoir | `net fluid example --profile reservoir`, run and verify | Default coupled two-reservoir/piston trajectory, equations, balances, reference and refinement checks are `LOCAL` |
| Fluid wave | `net fluid example --profile wave`, run and verify | Default periodic linear shallow-water fixture: 64 base cells, 64 steps, declared time/spatial refinements; analytic, mass, energy, speed and convergence checks are `LOCAL` |
| Thermal observer | Installed `operator_examples/thermal-source.json`, `ciw.scientific.execute/replay`, observation export | Synthetic Python reference posterior replays exactly with a fresh execution occurrence |
| Measured thermal fixture | Checked-in `examples/measured-thermal/source.json`, same scientific APIs | Explicit `test_fixture` input; held-out declared core comparison passes; posterior replays exactly while preserving the asset identity |
| Polymer | `net polymer demo --process injection_molding` | Synthetic injection-molding workflow and fresh numerical audit pass; report retains separate part-conformity and cooling decisions |
| Temperature | `temperature_contract.example_request`, `temperature_processing.process_request` | Hand-calculated joint covariance; incomplete uncertainty budget stays **indeterminate** and polymer temperature stays unavailable |
| Structural rewrite | `net rewrite example --profile thermal`, run and verify | Bounded structural split and fresh replay preserve the declared total 100 J/K heat capacity; no numerical simulation is claimed |

The scope is this explicit list, not every catalog domain or every feature within these domains. Built-in crush/plate variants and atmosphere have their own runnable guides and qualification paths; this timed campaign does not exercise them. General CFD, client integrations, external native Julia/Rust/C providers, private repositories, and SP1 proof generation require their own additional implementations or runtimes and qualification evidence. The thermal source contains an inherited Julia-oriented request declaration; the executed runtime is the built-in Python reference, which is identified in its retained records.

## Run against a wheel

Build the wheel from the exact candidate revision and record its full Git SHA. Create and activate a fresh virtual environment **outside** the checkout, then install that wheel. For example, from the checkout:

```text
python -m pip wheel . --no-deps --wheel-dir ../wheelhouse
python -m venv ../example-env
```

Activate `../example-env/bin/activate` on Linux or `..\example-env\Scripts\Activate.ps1` in PowerShell. Install the built wheel, using its actual path:

```text
python -m pip install ../wheelhouse/computational_instrumentation_workbench-0.1.0-py3-none-any.whl
```

Change to the parent directory, replacing `NET` with the checkout directory and `FULL_CANDIDATE_GIT_SHA` with the revision used to build the wheel:

```text
python -I NET/scripts/check_domain_examples.py --wheel wheelhouse/computational_instrumentation_workbench-0.1.0-py3-none-any.whl --source-revision FULL_CANDIDATE_GIT_SHA --output-dir domain-example-results
```

The directory must not already exist. `report.json` records all nine outcomes and elapsed seconds, Python/platform/package versions, candidate revision, runner digest, wheel SHA-256, checked installed package bytes, and artifact digests. Each example retains its own `outcome.json`, process log, numerical outputs and relevant workflow records. The measured fixture is copied and hashed. The wheel is bound using its installed direct-URL hash and a byte comparison of every packaged `ciw` file. Imported `ciw` modules must remain inside that installation.

`candidate_revision` is a caller-supplied checkout identity; the runner does not prove that a caller built the wheel from that Git commit. Wheel and script digests separately identify the bytes exercised. CI builds the wheel immediately after checking out `github.sha`, and uses that SHA in both the candidate and job-outcome manifests. Local runs with uncommitted edits must be described as such rather than presented as evidence for the unmodified base commit.

Each example gets at most 120 seconds, and the whole example loop shares a 540-second deadline. `--timeout-s` can reduce the per-example budget. Expired budgets are failures. These are execution limits, not a promise that network-dependent installation or CI queueing finishes in ten minutes; final report writing adds a small amount of filesystem work. For a timed run, use the actual `elapsed_s` values in its report.

## External numerical comparison and CI

The independent SciPy checks are a separate optional-dependency path. Install the same wheel with its pinned numerical extra:

```text
python -m pip install "../wheelhouse/computational_instrumentation_workbench-0.1.0-py3-none-any.whl[numerical-checks]"
python -I NET/scripts/check_external_numerics.py --output external-numerics.json
```

Run the second command from outside the checkout and adjust the wheel path for that working directory. This comparison checks reservoir trajectories against independent SciPy DOP853 integration and polymer cooling against a homogeneous cooling ODE plus a converged 64-term convective-slab solution. It distinguishes numerical agreement within the homogeneous model from discrepancy caused by internal temperature gradients; it does not turn synthetic agreement into physical validation. See [EXTERNAL_NUMERICAL_COMPARISONS.md](EXTERNAL_NUMERICAL_COMPARISONS.md).

[Review examples](../.github/workflows/review-examples.yml) builds and installs the wheel in a clean environment on Linux and Windows, runs the new regression tests against installed imports with zero skips, executes all timed examples, runs the separate SciPy check, and exercises the installed stdio server through the official MCP SDK. A separate Windows/Python 3.11 job checks tool discovery and execution-lane eligibility. `review-examples-ubuntu-latest` and `review-examples-windows-latest` artifacts contain the revision-bound outcomes even when a step fails. This additional workflow is evidence for these examples; it does not by itself satisfy the complete release qualification policy.
