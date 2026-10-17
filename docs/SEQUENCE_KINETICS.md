# Bounded sequence kinetics reference integration

## What this adds

This opt-in workload extends NET with two ordinary `Operation` objects:

* `sequence.markov-analyze.v1`: block-probability diagnostics, explicit conditional
  probabilities, sparse context transitions, information entropy and sequence queries.
* `sequence.binary-evolve.v1`: analytic independent binary-site transitions,
  preserving the complete initial joint distribution, including correlations.

Both use the existing `OperationRegistry`, trusted payload-schema extension seam,
`Session`, execution/result envelopes, content identities and workspace reopening.
There is no parallel ledger, state owner or native execution supervisor. Neither
operation claims verification authority. Static inspection does not run either
numerical operation. A replay allocates fresh native execution/result identities
while retaining the same scientific evidence identity.

Reference: T. Fischbacher, *Life at the Boundary of Chemical Kinetics and Program
Execution*, Physical Review E 112, 035309 (2025), DOI 10.1103/dyzr-m21s.
The selected fork and immutable pin are in
`third_party/chemical-kinetics-and-program-execution/reference.json`.

## Operator journey

After installing the change from a complete NET checkout:

```sh
net polymer sequence example --output sequence-request.json
net polymer sequence run sequence-request.json --output-dir sequence-original
net polymer sequence inspect sequence-original
net polymer sequence replay sequence-original --output-dir sequence-replay
```

The equivalent direct entry point is `python -m ciw.sequence_cli`.
Use new output directories; publication never replaces an existing bundle.
The example is an authored synthetic reference, not a polymer experiment or
radioisotope measurement. Source references bind declarations, not authenticity.
Its `run.v1` transport contains ONE declaration at a synthetic selection time.
Actual kinetic times are separately retained in the request and evolution result.

Python integration in a trusted process:

```python
from ciw import sequence_workflow
from ciw.session import Session

registry = sequence_workflow.registry()  # explicit trusted schema/provider setup
source = sequence_workflow.make_source(request)
session = Session(source, output_directory, operations=registry)
```

For read-only `Session.from_workspace`, call `sequence_workflow.register_schemas()`
first. Generic readers without this explicit extension setup may refuse these
unregistered payloads. Retained files cannot import or register providers.
Existing operation registries are not silently modified or replaced; callers can
register the two objects from `operations()` into their own registry.

## Scientific contract and representation

A request declares an ordered alphabet of 1..8 symbols, a block length of 1..8,
and all probabilities in lexicographic order, with at most 4096 states. The only
probability unit is `1`. Fixed absolute tolerance is 1e-12 for probability mass
and prefix/suffix marginal agreement. No clipping, normalization or epsilon prior
is applied. JSON is bounded to 256 KiB; nonfinite values, duplicate keys, booleans
in numeric fields and input-number underflow are rejected by the CLI boundary.

For each context w, p(w) is obtained by summing the following-symbol probabilities.
The conditional p(a|w) is p(wa)/p(w) ONLY when p(w)>0. A zero-mass context has a null
conditional row and no outgoing transfer edges; no arbitrary uniform completion
is introduced. Context edge orientation is explicit. It corresponds to the
upstream transfer matrix's `[target, source]` convention.

Matching prefix/suffix marginals means numerical *translation consistency along
the sequence*, not temporal equilibrium. Incompatible marginals yield REFUSE and
prevent kinetic execution. Reducible and disconnected support is retained rather
than forced into a unique stationary distribution by tiny positive probabilities.

Queries of length <= block length are retained-block marginals. Longer queries
require explicit `allow_markov_closure: true`; otherwise they return EXPAND with
no probability. The extension is a finite-order Markov model assumption, not
measured long-range sequence evidence. A positive long-query probability which
underflows has status UNDERFLOW, null probability and a finite log probability;
it does not become an impossible event.

Block entropy and conditional entropy are dimensionless Shannon quantities in
nats. Conditional entropy is labeled a Markov entropy rate only for compatible
marginals. It is not thermodynamic entropy, entropy production, free energy,
chemical potential or a Landauer bound. Those need separate physical models.

## Independent-site kinetic benchmark

The only evolving model has binary sites with declared rates a: 0->1 and b: 1->0,
in inverse seconds. The one-site transition matrix is evaluated analytically;
its tensor action is applied to the FULL initial block distribution. This avoids
assuming the initial sites are independent and does not require SciPy at runtime.
The implementation uses expm1 and explicit small-contribution checks rather than
subtracting a near-one exponential. Rate-time products and trajectory size are
bounded; positive contribution underflow refuses instead of silently losing support.

This is exact for the declared independent-site Markov model up to floating-point
arithmetic. It is NOT a general closure ODE, interacting-tape evolution,
copolymerization simulator, molecular mechanism or continuum rheology solver.
The reference's uniform three-symbol B->A decay canary has derivative:

```text
[0.375, 0.125, 0.125, -0.125, 0.125, -0.125, -0.125, -0.375]
```

That vector is retained as a published SOFTWARE fixture. Probability conservation
is not elemental mass conservation; symbol identities do not establish molecules.
The existing Catalyst/Cantera and thermochemical profiles are unchanged.

## Reference audit and exclusions

The source inspection found boundaries inappropriate for direct host import:

* `markov_tapes.py` loads a .so and initializes Gambit plus a canary on import.
* Its own comments identify possible native cleanup/REPL segfault behavior; this
  warning was read, not independently reproduced here.
* `mpp_from_spd` clips inputs and inserts a tiny positive prior. NET instead keeps
  impossible support and unknown conditional contexts explicit.
* The IVP helper returns `.y.T` without checking solver success; the derivative
  wrapper also prints unconditional debug output into stdout.
* The copolymerization script refers to undefined DATA_FILENAME while defining
  DATA_FILE. That script was inspected, not executed or changed.

No Gambit-C, GCC, JAX, Scheme programs, ctypes library, or host-code evaluator is
loaded by the new workload. No native wrapper is declared provisioned. A future
full interaction adapter must be a separately pinned provider behind NET/SCR's
bounded subprocess interface: explicit program definitions, step/branch/window
budgets, timeout/cancellation, return-status and complete-time-grid checks, raw
failure evidence, and independent reference qualification. A source code hash is
not runtime availability. The user-selected fork itself is unchanged.

## Validation and remaining integration gate

```sh
python -m unittest discover -s tests -p test_sequence_kinetics.py -v
python scripts/check_sequence_reference.py --output sequence-reference-audit.json
python -m unittest discover -s tests -p test_sequence_session.py -v
```

The first suite covers pure calculations, real Operation object callbacks,
source binding, immutable inputs, static validators and invalid-input behavior.
The separate SciPy checker constructs a full continuous-time Markov generator by
state enumeration and uses a matrix exponential, independently of the tensor
transition implementation. It covers 105 cases and 420 requested time points.

`test_sequence_session.py` is an explicit full-checkout native Session gate, not a
mock host. Dedicated CI also runs installed `net polymer sequence` commands outside
the checkout on Linux/Windows and Python 3.11/3.12. Local focused results do not
stand in for this gate; the publication/validation record states which ran.

Inspection verifies structure, bindings and declared domains without rerunning the
science. A coherently altered within-domain numerical result can pass inspection;
no independent-verifier or source-authentication claim is made. The separate
reference checker and fresh replay serve different purposes from reopening.
Local files assume a trusted filesystem; this is not a race-proof hostile-path
sandbox. Hardware control, canonical admission and physical qualification remain
outside this increment.
