# Retained render contract v1

The [writer](../examples/_render_json.py) and its additive
[JSON Schema](../examples/retained-render.schema.json) describe host-owned
presentation records. Godot and Bevy consume these records independently.
They neither call each other nor write them back into Session. A rendered
record grants no execution, state-admission or equipment authority.

| Field | Writer contract and projector behavior |
| --- | --- |
| `status`, `source` | Retain the host's status and source. Display status comparison trims whitespace and ignores case. |
| `cards` | Array or named object of retained cards. Both projectors guard `status` and `verification_status` using the record's freshness flag. |
| `may_authorize` | Writer always sets `false`; projectors supply no command path even if an external file says `true`. |
| `presentation_only` | Writer always sets `true`. |
| `claim_scope` | Defaults to `computational-integrity-only`; a caption does not qualify a provider or measurement. |
| `fresh_verifier_occurrence` | Only the JSON boolean `true` permits a VERIFIED label. Missing, false, string and numeric flags do not. Rendering does not create or independently validate a verifier occurrence. |
| `series`, paths and domain fields | Preserve existing payloads and identities as extensions. Unsupported fields remain in the retained input; each projector displays its supported subset. |

The existing status vocabulary is `LIVE`, `STALE`, `UNAVAILABLE`, `REFUSED`,
`HELD`, `RECONCILED`, `SATISFIED`, `VIOLATED`, `REQUEST_EVIDENCE` and
`HISTORICAL`. Existing domain outcomes are preserved; they do not become new
authority states. HISTORICAL always displays as
`retained_runtime_report_requires_fresh_verification`. VERIFIED without a
strictly true freshness flag displays the same historical warning. An empty
status is UNAVAILABLE. Missing input is STALE; malformed input refuses.

Serialization uses finite JSON numbers and preserves Python binary64 values.
The Bevy summary retains binary64 path coordinates. Its GPU projection fits
bounds in binary64 before converting display coordinates to binary32, so a
large common coordinate offset does not collapse a short path. Supported Bevy
geometry is `trajectories.{name}.points`, `path`, `path.points`, `points` or
`points.points`, with two or three finite coordinates per point. Its existing
16 MiB input, 100,000 point, 256 card and coordinate-range limits remain.
The Godot teaching view displays retained cards and status; it is not a path
renderer. The schema describes writer output, while legacy minimal records
remain readable by the projectors.

## One host entry point

Use the existing catalog exporter to emit a selected teaching record and,
optionally, open either projector. The default still exports all ten families.

```sh
python examples/workflows/emit_all_usecases.py \
  --scenario reservoir-disagreement --output-root /absolute/path/new-run \
  --receipt /absolute/path/new-run/bevy-receipt.json \
  --client bevy --binary /absolute/path/bevy-render-view
```

Use `--client godot --binary /absolute/path/godot` for card inspection in the
same teaching view. `--frames 240 --timeout 120` bounds a validation run;
otherwise the operator closes the window. `--dry-run` validates without
emitting or launching. The executable comes only from the host's explicit
argument; catalog and render content never select a program, shell or module.
The complete catalog is validated before any output is written.

The receipt retains catalog/output hashes, binary hash, argument list, process
outcome, input stability and bounded process output. A zero exit is process
evidence; visible content needs a separate display observation. Native-provider
qualification and fresh scientific verification remain NOT_RUN for teaching
exports. A failed or timed-out launch returns failure and retains its outcome.

Godot's `--retained-render` launch mode instantiates the existing teaching view
without adding or connecting the Session client. The regular Godot Workbench
and Oscillator paths continue using Session. The separately registered
[Bevy projectile runtime](INTERACTIVE_SIMULATION.md) keeps its bounded
computational execution contract; it is distinct from the retained projector.

New presentation work must name its input record, missing/stale/refusal behavior
and existing operation being projected. Analog and Circle components remain
unattached until a selected contract and executed journey justify attachment.
This contract does not change the frozen baseline or close hosted gates.
