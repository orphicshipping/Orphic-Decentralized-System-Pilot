# GaugeLink / NET scientific projection guards

Companion to [GaugeLink delivery and Digital Inspection](GAUGELINK_DELIVERY.md).

This follow-on to `3bbcef826d16291a1515ba76696c96677462153e` strengthens the
existing Link/scalar path; no new operation, customer command, runtime, native
schema, data bridge or physical authority is introduced.

**A source quality flag cannot disappear during interpretation.** Link v1 accepts
bounded declared flags but has no registered vendor-specific interpretation of
them. Native projection now withholds a flagged record with
`quality_flags_require_review`, while keeping its exact original bytes, value,
flag strings and identity. It does not guess that an unfamiliar flag is benign.
The finite scalar-study decoder refuses a stream containing any flagged chunk
rather than removing a reading and computing an unmarked mean of the remainder.
These are conservative use gates, not a finding that the sensor is defective.

The operator view shows **Source quality: Flagged review required** and a next
step when flags exist. Advanced lists original measurement/flag bindings even
when decoding was refused. With no flags the wording is **No flags declared**,
not “quality verified.” Status is derived from validated source records, never
from a saved HTML label. Signed custody can succeed while interpretation remains
blocked: byte retention does not override an instrument's declared quality flag.

**Native timestamps satisfy the existing nearest-tick rule exactly.** The scalar
exporter previously tested `round(seconds * tick_hz)` using a second rounded
floating-point operation. At a relative interval of `9007199254740992` ticks and
`1000000000` Hz this falsely accepted a timestamp whose exact represented value
maps to tick `9007199254740993`. The existing generic Link projection already
used rational checking and correctly withheld it. Scalar projection now uses the
same criterion, evaluating the exact rational value of the stored binary64 time
before nearest-even integer rounding. This fixes agreement at the two boundaries,
not physical clock synchronization or a new timestamp precision guarantee.

The original 64-bit tick stays in the retained row. A timestamp that cannot meet
the existing rule receives `native_time_precision_insufficient`; it is not
rounded into a different observation time or replaced by host wall time. Short
relative intervals remain usable even when absolute ticks are large. The tests
include 287 seeded/boundary conversions across seven frequencies using an
independent `as_integer_ratio`/integer-division oracle, without using the
implementation's Fraction rounding. Python's exact-float rational semantics are
documented in <https://docs.python.org/3/library/fractions.html>.

**A completed audit operation is not necessarily a passing audit.** The original
four Session operations can all complete while the independent arithmetic audit
reports FAIL. Native scalar export now requires that retained audit's PASS before
creating any output directory. The negative test deliberately perturbs the
primary covariance calculation and leaves the independent Fraction audit, Session,
DAG and payload validators unchanged. Its failed result and raw investigation
remain retained, but no native result/covariance export is issued. This gate does
not claim to protect arbitrary external consumers that bypass this export API.

The successful flag-free numerical outputs, uncertainty ancestry, original wire
and request formats, custody semantics and customer commands are unchanged.
Normal pre-change investigations can still reopen and replay without rewriting
the originals. Previously accepted flagged interpretations are not silently
migrated or re-certified under the stricter policy; retain the original evidence
and code identity for historical review. This does not authorize deleting flags,
relabeling a measurement, or overriding physical qualification.

The existing source/installed qualifier now also includes
`tests/test_link_scientific_guards.py` under `--with-delivery`. Parent data and
measurement/pinwheel integrations (#185/#200/#208/#210), account/consent #206 and
private GaugeLink source are not edited or merged. Their combined deployment
qualification remains separate. Exact dependency pins, actual hardware calibration,
physical clocks, ARM/SBC, production transport and SP1 remain independent gates.
