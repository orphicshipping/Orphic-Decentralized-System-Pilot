# Contributing to NET

Thank you for contributing to the Notations Engineering Terminal (NET).

NET is a scientific and industrial workbench whose correctness depends on preserving identity, evidence, execution, verification, and qualification boundaries. Contributions should extend the existing substrate rather than create parallel architecture unless a concrete investigation cannot be completed with the current components.

Read [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) before making changes. It is the controlling development guide for validation commands, architecture boundaries, provider qualification, CI routing, and investigation closure.

## Before you start

A contribution should identify:

- the investigation, workflow, defect, or capability being addressed;
- the smallest missing capability or correction;
- the affected identities, schemas, providers, adapters, or execution surfaces;
- expected evidence and verification behavior;
- exact acceptance criteria;
- unresolved provider, platform, hardware, or physical-validation gates.

Review current branches and pull requests before implementing overlapping work. Use an isolated branch or checkout. Do not overwrite another contributor's work or force-push shared history.

## Core invariants

Preserve the existing NET architecture unless evidence requires a change.

In particular:

- preserve separate evidence, operation, execution, result, and verification identities;
- preserve source identity, runtime identity, units, frames, clocks, covariance ordering, uncertainty declarations, and assumptions;
- compute from full-resolution retained records and keep missing observations explicit;
- do not silently repair, reinterpret, diagonalize, normalize, or discard scientific state;
- preserve raw evidence when deriving calibrated or transformed values;
- validate saved records without executing numerical providers;
- keep provider/runtime bindings explicit;
- retain refusal states and unresolved gates as first-class outcomes;
- do not equate rendered output, replay equality, passing software tests, or documentation with independent verification or physical qualification;
- preserve bounded authority between observation, recommendation, execution, and physical control.

A new workload, provider, proof system, representation, instrument, or engine should plug into the existing substrate instead of creating a conceptually separate path.

## Contribution workflow

1. Reconcile with current `main` and relevant open work.
2. Build the smallest concrete increment.
3. Run it.
4. Observe the actual result.
5. Fix defects.
6. Run the relevant verification and regression gates.
7. Record exact source identities, commands, results, skips, failures, artifacts, and blockers.
8. Open a focused pull request.

Documentation changes should describe implemented behavior and measured limitations. They do not close executable, numerical, provider, hardware, or qualification gates by themselves.

## Validation

The baseline development checks are:

```sh
python -m pip install -e '.[dev]'
python -m pytest -q
python scripts/check_adapters.py
```

Additional provider, geometry, free-energy, Godot, deployment, installed-wheel, hardware, refurbishment, and release gates are documented in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) and the relevant operation guides.

Run the narrowest applicable checks during iteration, then the complete required gates for the files and contracts you changed. Required checks that cannot run must be reported as unavailable or blocked. Do not convert skips, missing providers, inaccessible hardware, queued CI, or incomplete qualification into passes.

## Scientific and engineering claims

Every quantitative or capability claim should be tied to reproducible evidence appropriate to its scope.

Distinguish:

- implementation from installation;
- installation from integration;
- integration from executed verification;
- software simulation from measured physical validation;
- calibration from qualification;
- qualified observation from command authority;
- candidate behavior from released behavior.

Where a model, estimator, calibration, or physical instrument is involved, document the applicable domain, operating range, reference data, uncertainty or residual behavior, and known limitations.

## External tools and providers

External software remains governed by its own license and provenance. Record reproducible source pins or version identities and do not imply that cataloguing or adapting an external tool changes its license.

Provider-specific code belongs behind the relevant NET boundary. Do not commit credentials, private provider source, restricted datasets, customer artifacts, proprietary drivers, or secrets.

## Hardware and physical systems

Cyber-physical contributions require explicit device, sensor, controller, interface, clock, calibration, capability, and authority identities as applicable.

A new hardware integration should start at the lowest required authority. Observation and command paths must remain distinguishable. Emergency stops, safety PLCs, protective interlocks, certified controls, and other independent safety systems must not be bypassed by a NET contribution.

Passing software checks does not qualify a machine, measurement chain, manufacturing process, or product.

## Pull requests

A useful pull request should include:

- purpose and scope;
- exact branch and source revision;
- files and contracts changed;
- validation commands actually executed;
- results, including failures and skips;
- provider/runtime or hardware dependencies;
- retained artifacts or evidence where applicable;
- compatibility or migration impact;
- known limitations and remaining blockers;
- licensing or provenance implications for imported material.

Keep unrelated changes separate where practical.

## Security

Do not report vulnerabilities publicly. Follow [SECURITY.md](SECURITY.md) for suspected security issues, especially findings involving credentials, retained evidence, provider execution, supply-chain compromise, privilege/authority escalation, or cyber-physical control.

## Licensing

First-party NET application code follows the repository's existing licensing policy in [docs/LICENSING.md](docs/LICENSING.md) and [LICENSE](LICENSE), subject to explicit component exceptions. Imported components retain their own licenses and notices.

By submitting a contribution, you represent that you have the right to submit it under the applicable repository and component terms. Do not remove upstream copyright, license, provenance, or attribution notices.
