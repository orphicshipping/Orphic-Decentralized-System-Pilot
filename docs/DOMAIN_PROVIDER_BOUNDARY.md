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
