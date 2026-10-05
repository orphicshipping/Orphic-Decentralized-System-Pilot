"""Manufacturing identities, fail-closed envelopes, and offline CLI regressions."""
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import io
import json
from pathlib import Path
import random
import tempfile
import unittest

from ciw import microfab as m
from ciw import microfab_cli as cli
from ciw.core.identities import content_identity


class MicrofabTests(unittest.TestCase):
    def setUp(self):
        self.plan = m.example_plan()

    def test_candidate_is_not_qualified(self):
        result = m.compile_envelope(self.plan)
        self.assertEqual(result['status'], 'CANDIDATE')
        self.assertEqual(result['envelope'], self.plan['requested'])
        self.assertEqual(result['authority'], m.AUTHORITY)
        self.assertEqual(result['authority']['hardware_actuation'], 'not_performed')
        self.assertEqual(result['authority']['process_qualification'], 'not_established')

    def test_pure_deterministic_compilation(self):
        original = deepcopy(self.plan)
        one, two = m.compile_envelope(self.plan), m.compile_envelope(self.plan)
        self.assertEqual(one, two)
        self.assertEqual(self.plan, original)
        one['bindings']['cavity_ids'].append('changed')
        one['authority']['hardware_actuation'] = 'forged'
        self.assertEqual(self.plan, original)
        self.assertEqual(m.compile_envelope(self.plan), two)

    def test_content_reference_matches_native_encoder(self):
        result = m.compile_envelope(self.plan)
        ref = result.pop('envelope_ref')
        self.assertEqual(ref, content_identity(result))
        self.assertEqual(result['plan_ref'], content_identity(self.plan))
        for role in m.COMPONENTS:
            self.assertEqual(result['component_refs'][role], content_identity(self.plan[role]))

    def test_reconstruction(self):
        result = m.compile_envelope(self.plan)
        audit = m.verify_envelope(self.plan, result)
        self.assertEqual(audit['status'], 'PASS')
        self.assertEqual(audit['scope'], 'deterministic_reconstruction_only')

    def test_resealed_forgery_rejected(self):
        result = m.compile_envelope(self.plan)
        result['envelope']['melt_temperature']['upper'] += 100
        result.pop('envelope_ref')
        result['envelope_ref'] = content_identity(result)
        with self.assertRaises(ValueError):
            m.verify_envelope(self.plan, result)

    def test_unknown_and_conflict_precedence(self):
        self.plan['material']['limits']['melt_temperature'] = None
        self.plan['quality']['limits']['injection_pressure']['lower'] = 6000000
        self.plan['quality']['limits']['injection_pressure']['upper'] = 7000000
        result = m.compile_envelope(self.plan)
        self.assertEqual(result['status'], 'REFUSE')
        self.assertIn('material.melt_temperature', result['unknowns'])
        self.assertIsNone(result['envelope']['melt_temperature'])

    def test_equal_closed_boundary(self):
        self.plan['machine']['limits']['melt_temperature']['lower'] = 500
        result = m.compile_envelope(self.plan)
        self.assertEqual(result['status'], 'CANDIDATE')
        self.assertEqual(result['envelope']['melt_temperature']['lower'], 500)
        self.assertEqual(result['envelope']['melt_temperature']['upper'], 500)

    def test_no_hidden_unit_conversion(self):
        self.plan['requested']['melt_temperature']['unit'] = 'degC'
        with self.assertRaises(ValueError):
            m.compile_envelope(self.plan)

    def test_declared_source_is_not_authenticated(self):
        self.plan['source_kind'] = 'declared'
        result = m.compile_envelope(self.plan)
        self.assertEqual(result['source_kind'], 'declared')
        self.assertEqual(result['authority']['source_authentication'], 'not_established')

    def test_duplicate_cavities(self):
        self.plan['tool']['cavity_ids'] *= 2
        with self.assertRaises(ValueError):
            m.compile_envelope(self.plan)

    def test_bounded_depth(self):
        value = None
        for _ in range(18):
            value = [value]
        with self.assertRaises(ValueError):
            m.bounded(value)

    def test_random_intersections_stay_within_all_supplied_bounds(self):
        rng = random.Random(271828)
        for _ in range(200):
            plan = deepcopy(self.plan)
            axis = 'melt_temperature'
            intervals = [plan['requested'][axis]] + [
                plan[role]['limits'][axis] for role in m.REQUIRED if axis in m.REQUIRED[role]]
            for interval in intervals:
                a, b = sorted((rng.randint(420, 520), rng.randint(420, 520)))
                interval.update(lower=a, upper=b)
            result = m.compile_envelope(plan)
            # Independent finite-integer oracle, not the compiler's max/min algorithm.
            feasible = [x for x in range(420, 521) if all(i['lower'] <= x <= i['upper'] for i in intervals)]
            if feasible:
                self.assertEqual(result['status'], 'CANDIDATE')
                self.assertEqual(result['envelope'][axis]['lower'], feasible[0])
                self.assertEqual(result['envelope'][axis]['upper'], feasible[-1])
            else:
                self.assertEqual(result['status'], 'REFUSE')


def add_case(name, function):
    function.__name__ = name
    setattr(MicrofabTests, name, function)


for role, axes in m.REQUIRED.items():
    for axis in axes:
        def missing(self, role=role, axis=axis):
            self.plan[role]['limits'][axis] = None
            result = m.compile_envelope(self.plan)
            self.assertEqual(result['status'], 'HOLD')
            self.assertIsNone(result['envelope'][axis])
            self.assertIn(f'{role}.{axis}', result['unknowns'])
        add_case(f'test_missing_{role}_{axis}_holds', missing)

for axis in m.AXES:
    def requested_missing(self, axis=axis):
        self.plan['requested'][axis] = None
        self.assertEqual(m.compile_envelope(self.plan)['status'], 'HOLD')
    add_case(f'test_missing_requested_{axis}_holds', requested_missing)
    def conflict(self, axis=axis):
        interval = self.plan['quality']['limits'][axis]
        interval.update(lower=self.plan['requested'][axis]['upper'] + 1,
                        upper=self.plan['requested'][axis]['upper'] + 2)
        result = m.compile_envelope(self.plan)
        self.assertEqual(result['status'], 'REFUSE')
        self.assertIsNone(result['envelope'][axis])
    add_case(f'test_conflicting_{axis}_refuses', conflict)

for role, prefix in [('part', 'material'), ('lot', 'material'),
                     ('tool', 'part'), ('quality', 'part'), ('order', 'part')]:
    for field in ('id', 'revision'):
        def mismatch(self, role=role, prefix=prefix, field=field):
            self.plan[role][f'{prefix}_{field}'] += '.wrong'
            self.assertEqual(m.compile_envelope(self.plan)['status'], 'REFUSE')
        add_case(f'test_mismatched_{role}_{prefix}_{field}_refuses', mismatch)

for role in m.COMPONENTS:
    def identity(self, role=role):
        before = m.compile_envelope(self.plan)
        self.plan[role]['source_ref'] = content_identity({'changed': role})
        after = m.compile_envelope(self.plan)
        self.assertNotEqual(before['plan_ref'], after['plan_ref'])
        self.assertNotEqual(before['component_refs'][role], after['component_refs'][role])
    add_case(f'test_{role}_source_changes_identity', identity)
    def missing_key(self, role=role):
        del self.plan[role]['revision']
        with self.assertRaises(ValueError):
            m.compile_envelope(self.plan)
    add_case(f'test_{role}_revision_required', missing_key)

for disposition, status in [('AVAILABLE', 'CANDIDATE'), ('HOLD', 'HOLD'), ('QUARANTINED', 'REFUSE')]:
    def lot_state(self, disposition=disposition, status=status):
        self.plan['lot']['disposition'] = disposition
        self.assertEqual(m.compile_envelope(self.plan)['status'], status)
    add_case(f'test_lot_{disposition.lower()}', lot_state)

for label, value in [('bool', True), ('nan', float('nan')), ('infinity', float('inf')),
                     ('negative', -1), ('huge', 1e150), ('string', '450')]:
    def endpoint(self, value=value):
        self.plan['requested']['melt_temperature']['lower'] = value
        with self.assertRaises(ValueError):
            m.compile_envelope(self.plan)
    add_case(f'test_invalid_endpoint_{label}', endpoint)

for label, value in [('bool', True), ('zero', 0), ('fraction', 1.5), ('too_many', 1000001)]:
    def order_quantity(self, value=value):
        self.plan['order']['quantity'] = value
        with self.assertRaises(ValueError):
            m.compile_envelope(self.plan)
    add_case(f'test_invalid_order_quantity_{label}', order_quantity)


class MicrofabCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def run_cli(self, *argv):
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            code = cli.main(list(map(str, argv)))
        return code, out.getvalue(), err.getvalue()

    def test_complete_offline_journey(self):
        plan, result = self.root / 'plan.json', self.root / 'envelope.json'
        self.assertEqual(self.run_cli('example', '--output', plan)[0], 0)
        self.assertEqual(self.run_cli('compile', plan, '--output', result)[0], 0)
        self.assertEqual(self.run_cli('verify', plan, result)[0], 0)
        before = result.read_bytes()
        self.assertEqual(self.run_cli('compile', plan, '--output', result)[0], 1)
        self.assertEqual(result.read_bytes(), before)

    def test_held_report_saved_with_nonzero_exit(self):
        plan, result = self.root / 'plan.json', self.root / 'envelope.json'
        value = m.example_plan()
        value['lot']['disposition'] = 'HOLD'
        cli.save_new(plan, value)
        self.assertEqual(self.run_cli('compile', plan, '--output', result)[0], 2)
        code, stdout, _ = self.run_cli('verify', plan, result)
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stdout)['status'], 'PASS')
        self.assertEqual(json.loads(stdout)['envelope_status'], 'HOLD')

    def test_invalid_input_creates_no_output_directory(self):
        plan = self.root / 'invalid.json'
        plan.write_text('{}')
        result = self.root / 'must-not-exist' / 'envelope.json'
        self.assertEqual(self.run_cli('compile', plan, '--output', result)[0], 1)
        self.assertFalse(result.parent.exists())

    def test_symlink_input_and_output_rejected(self):
        target, link = self.root / 'target', self.root / 'link'
        cli.save_new(target, m.example_plan())
        try:
            link.symlink_to(target)
        except OSError:
            self.skipTest('Host does not permit creation of a test symlink')
        with self.assertRaises(ValueError):
            cli.load(link)
        with self.assertRaises(FileExistsError):
            cli.save_new(link, {})
        self.assertEqual(cli.load(target), m.example_plan())

    def test_oversized_file_rejected(self):
        path = self.root / 'large.json'
        path.write_bytes(b' ' * (m.MAX_BYTES + 1))
        with self.assertRaises(ValueError):
            cli.load(path)


for label, source in [('duplicate', '{"a":1,"a":2}'), ('nan', '{"a":NaN}'),
                      ('infinity', '{"a":Infinity}'), ('underflow', '{"a":1e-999}'),
                      ('overflow', '{"a":1e999}'), ('empty', ''), ('trailing', '{} {}')]:
    def invalid_json(self, source=source):
        path = self.root / 'bad.json'
        path.write_text(source)
        with self.assertRaises(ValueError):
            cli.load(path)
    setattr(MicrofabCliTests, f'test_reject_json_{label}', invalid_json)


if __name__ == '__main__':
    unittest.main()
