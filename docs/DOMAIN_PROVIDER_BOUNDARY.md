# Optional domain providers and retained-result dependencies

This is an additive public integration boundary, not publication of a numerical
engine. The private domain provider is installed explicitly by its operator and
is not bundled, downloaded or automatically installed by NET.

## Trusted extension registration

After registering an offline payload validator with the existing
`register_payload_validator`, installed trusted code may call
`register_dependency_fields(operation_id, ("candidate",))`. Each field names a
parameter containing a complete retained operation-result occurrence.

The registration must name an existing extension schema, not a built-in
operation. It accepts 1..8 unique identifier field names and cannot be replaced.
Scientific requests and saved workspaces cannot register validators, load code,
choose packages or supply dependency contracts.

Before provider execution, every declared dependency must equal the exact result
stored under its result ID. Missing, altered and unretained occurrences refuse.
Completed saved executions are checked again against retained prerequisites and
must bind the same source evidence. Existing built-in dependency rules remain.

This does not establish that an exact retained result is scientifically correct.
Domain-specific validators must still enforce the expected operation, payload,
verification outcome, model/field binding and scientific preconditions.

An extension may also register one trusted offline whole-result check with
`register_result_dependency_validator(operation_id, validator)`. The callable
receives detached copies of the result and the complete retained result map.
It can check relationships such as an outer runtime matching the payload runtime
or a verification occurrence being unique, including operations with no result
prerequisites. Registration requires an existing extension payload schema and
rejects unknown operations, built-ins, duplicate registrations and non-callables.
These checks run before workspace restoration writes output. They must not bind
or execute a provider. They supplement, rather than replace, payload validation
and exact retained-result matching. Matching uses canonical JSON, so booleans
cannot stand in for numbers through Python equality.

Session, workspace schemas, canonical evidence identities, ordinary result
verification fields and existing provider grants are unchanged. There is no new
state owner, scheduler, admission mechanism, execution authority or proof profile.

## Optional command

`net domains --help` works without the private provider. Other commands delegate
only to the explicitly installed `notation_domains.cli` module. Absence produces
REFUSE, not an automatic installation or a numerical fallback. Dependencies
missing *inside* an installed provider are not misreported as provider absence.

The private implementation may expose bind/example/run/demo/inspect/verify/replay.
The public dispatcher does not itself implement domains, heat, mapping, LBM,
learning, device access or an SCR native-host profile. The operator remains
responsible for selecting trusted provider code and commissioning its environment.

## Original local qualification

Based on public source revision `052124f2904ee6ceab1c256961d11b6865486424`.
The package sources are unchanged from the retained CI wheel at `0091492b...`;
GitHub's compare endpoint showed only two intervening documentation changes.

The 20 generic tests in `tests/test_extension_dependencies.py` passed both from
source and from the noneditable candidate wheel outside the source directory,
with zero failures, errors or skips. Repetition is not 40 distinct cases. A wider
private integration suite and the existing 18-check polymer qualifier also ran;
private source and numerical evidence are deliberately not published here.

Local environment: Linux, CPython 3.13.5, pytest 9.0.2, NumPy 2.3.5 and websockets
16.0. The wheel was built from the retained base package plus the tested overlay,
not a complete repository clone. Offline dependencies were copied explicitly into
the installed environment. NumPy differs from the unchanged project pin 2.4.3.
This is not pinned-environment or full-repository regression qualification.

The added workflow runs the public tests against an installed candidate on
Linux/Windows and Python 3.11/3.13. A workflow definition is not a successful
hosted run. Windows, broader affected repository tests, native SCR execution,
physical validation, state admission and equipment authority remain unestablished.

## Subsequent pinned-environment qualification

The additive whole-result hook and strict dependency identity checks were tested
on Linux with CPython 3.12.14, pytest 9.0.2, NumPy 2.4.3 and websockets 16.0.
The 25 public boundary tests passed as part of 234 installed-wheel checks of the
public host and two separately installed private providers (49 additional
subtests, no skips). All installed host and provider implementation files were
byte-compared with their newly built noneditable wheels. Test imports were bound
to that installation, outside the source tree; dependencies were provisioned
offline from explicit installed distributions without editable bindings.

Both private provider workflows used freshly compiled Linux native engines and
independent checkers through the existing Session lifecycle. Installed command
journeys exercised retained inspection, fresh verification and replay while
confirming that original files stayed unchanged. Separate Julia-to-C++ checks
also ran. This evidence does not establish Windows native qualification, SCR
native-host integration, physical validation, admission or equipment authority.



## Source and exact-content hardening — 5 October 2026

The execution boundary now checks a dependency's source evidence **before**
reading the provider runtime or invoking it, not only when reopening results.
`validate_request_dependencies` accepts keyword-only `evidence_id`; the existing
runner supplies its validated source ID. A direct caller checking an extension
with registered result dependencies must also supply this context. Existing
built-in three-argument calls remain compatible.

Exact retained content is compared using NET's existing canonical JSON encoder,
not Python dictionary equality: `False` and `0`, or `True` and `1`, must not
substitute for each other in an otherwise equal prerequisite. The same check
applies to live execution, saved dependency validation and execution/result binding. No canonical encoder,
source schema, covariance tolerance, independent checker or provider pin changes.

Nine new public regressions establish the defect and repair: eight fail against
the original PR head and all nine pass after the fix. The public selection now
contains 29 extension cases, plus the existing 20 operation-runner and 40 covariance
artifact cases: 89 distinct public tests. The hosted matrix requires all 89 to
complete without skips, and checks installed import provenance outside checkout.
The local combined public/private integration subset contains 90 cases (29+61),
and the complete selected set contains 150 distinct cases (89+61). Historical
results above remain attached to their original source; repeating a suite does
not create additional distinct cases.

Parent data integration remains separately owned by PR #185. Its untouched native
state/covariance envelopes and Workbench source module were byte-checked and used
in a local compatibility campaign. Keep its retained-source checks and this PR's
retained-result checks when composing the three shared files. The new source
context keyword belongs on the existing runner call; it must not be dropped when
adding the parent's adjacent data-operation checks. No new field bridge or parent
schema is introduced here. The selected two-module compatibility campaign is not
a qualification of the entire parent branch.

A separate current-main compatibility wheel was assembled after its complete
`src/ciw` tree matched `0b462bbb9ab1f6beddc3e187a1999e499688cd11` for main
`0374076695475669188d61698e4656ced2263e01`. With the four public integration
modules overlaid, the installed package passed 232 selected tests: the 150 cases
above plus all 82 current-main microfab cases. This is complete package-source
reconciliation and selected regression, not recovery of every monorepo test,
fixture, provider checkout or Git history. It does not change the declared
NumPy 2.4.3 requirement or convert the local NumPy 2.3.5 environment into a
pinned qualification.


## Composite reconciliation

The integrated boundary retains both changes: early source-context and exact
content validation from PR #184, and detached whole-result checks before restore
writes from PR #190. All typed-data source checks remain in the runner before
runtime lookup. The merged public selection has 34 extension cases, 20 runner
cases and 40 covariance cases; the hosted matrix requires exactly 94 cases
without skipped, failed or errored tests. Historical counts above describe the
individual revisions and are not combined into a larger qualification claim.
