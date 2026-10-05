"""Reference and adversarial tests for the bounded sequence adaptation."""
from copy import deepcopy
from fractions import Fraction
import hashlib
import itertools
import json
import math
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from ciw import sequence_kinetics as k
from ciw import sequence_workflow as w
from ciw.sequence_cli import load_request
from ciw.core.identities import canonical_json, content_identity, evidence_id
from ciw.operations.registry import OperationRegistry


def stationary(n=2, length=3, size=127, seed=271828):
    rng = random.Random(seed)
    tape = [rng.randrange(n) for _ in range(size)]
    counts = [0] * n ** length
    for start in range(size):
        index = 0
        for j in range(length):
            index = index * n + tape[(start + j) % size]
        counts[index] += 1
    req = k.example_request()
    req.update(alphabet=[str(x) for x in range(n)], block_length=length,
               probabilities=[c / size for c in counts], queries=[], evolution=None)
    return req, counts


class SequenceTests(unittest.TestCase):
    def setUp(self):
        self.req = k.example_request()

    def test_upstream_decay_canary(self):
        # Published framework/markov_tapes.py::_run_validation, not self-generated.
        expected = [0.375, 0.125, 0.125, -0.125, 0.125, -0.125, -0.125, -0.375]
        self.assertEqual(k.evolve_binary(self.req)['initial_derivative_per_s'], expected)

    def test_independent_decay_full_joint_analytic(self):
        out = k.evolve_binary(self.req)
        for sample in out['samples']:
            p_b = 0.5 * math.exp(-sample['time_s'])
            for index, p in enumerate(sample['probabilities']):
                ones = index.bit_count()
                self.assertAlmostEqual(p, p_b ** ones * (1 - p_b) ** (3 - ones), delta=2e-15)

    def test_stationary_bernoulli_entropy(self):
        out = k.analyze(self.req)
        self.assertEqual(out['status'], 'PASS')
        self.assertAlmostEqual(out['block_entropy_nats'], 3 * math.log(2))
        self.assertAlmostEqual(out['markov_entropy_rate_nats_per_symbol'], math.log(2))
        self.assertEqual(out['queries'][0]['probability'], .5)
        self.assertEqual(out['queries'][1]['probability'], .25)
        self.assertEqual(out['queries'][2]['status'], 'EXPAND')
        self.assertIsNone(out['queries'][2]['probability'])

    def test_explicit_longer_sequence_closure(self):
        self.req['allow_markov_closure'] = True
        out = k.analyze(self.req)
        self.assertEqual(out['queries'][2]['probability'], 1/16)
        self.assertEqual(out['queries'][2]['scope'], 'finite_order_markov_closure')

    def test_zero_context_is_not_uniform_or_smoothed(self):
        self.req.update(block_length=2, probabilities=[1., 0., 0., 0.], queries=[['B']], evolution=None)
        out = k.analyze(self.req)
        self.assertEqual(out['zero_mass_context_indices'], [1])
        self.assertIsNone(out['conditional_probabilities'][1])
        self.assertEqual(out['conditional_probabilities'][0], [1., 0.])
        self.assertEqual(out['queries'][0]['probability'], 0.)
        self.assertEqual(out['markov_entropy_rate_nats_per_symbol'], 0.)
        self.assertEqual(out['transfer_edges'], [{'from_context': 0, 'to_context': 0, 'symbol': 'A', 'probability': 1.}])

    def test_disconnected_stationary_mixture_stays_disconnected(self):
        self.req.update(block_length=2, probabilities=[.5, 0., 0., .5], queries=[], evolution=None)
        out = k.analyze(self.req)
        self.assertEqual(out['status'], 'PASS')
        self.assertEqual(out['conditional_probabilities'], [[1, 0], [0, 1]])
        self.assertEqual(out['markov_entropy_rate_nats_per_symbol'], 0.)
        self.assertAlmostEqual(out['block_entropy_nats'], math.log(2))

    def test_nonstationary_window_refuses(self):
        self.req.update(block_length=2, probabilities=[0., 1., 0., 0.])
        out = k.analyze(self.req)
        self.assertEqual(out['status'], 'REFUSE')
        self.assertIsNone(out['markov_entropy_rate_nats_per_symbol'])
        self.assertEqual(out['queries'], [])
        with self.assertRaisesRegex(ValueError, 'incompatible'):
            k.evolve_binary(self.req)

    def test_length_one_iid_closure(self):
        self.req.update(block_length=1, probabilities=[.75, .25], queries=[['B', 'A', 'B']],
                        allow_markov_closure=True, evolution=None)
        out = k.analyze(self.req)
        self.assertEqual(out['contexts'], [[]])
        self.assertEqual(out['queries'][0]['probability'], .25*.75*.25)

    def test_one_symbol_alphabet(self):
        self.req.update(alphabet=['O'], block_length=4, probabilities=[1], queries=[['O']*6],
                        allow_markov_closure=True, evolution=None)
        out = k.analyze(self.req)
        self.assertEqual(out['queries'][0]['probability'], 1.)
        self.assertEqual(out['markov_entropy_rate_nats_per_symbol'], 0.)

    def test_query_underflow_is_not_impossible_event(self):
        self.req.update(block_length=1, probabilities=[1., 1e-100], queries=[['B']*10],
                        allow_markov_closure=True, evolution=None)
        out = k.analyze(self.req)['queries'][0]
        self.assertEqual(out['status'], 'UNDERFLOW')
        self.assertIsNone(out['probability'])
        self.assertAlmostEqual(out['log_probability_nats'], -1000*math.log(10))

    def test_zero_probability_long_sequence(self):
        self.req.update(block_length=2, probabilities=[.5, 0, 0, .5], queries=[['A','A','B','B']],
                        allow_markov_closure=True, evolution=None)
        self.assertEqual(k.analyze(self.req)['queries'][0]['probability'], 0.)

    def test_inactive_rates_identity(self):
        self.req['evolution']['rate_1_to_0_per_s'] = 0
        out = k.evolve_binary(self.req)
        self.assertEqual(out['initial_derivative_per_s'], [0.] * 8)
        for sample in out['samples']:
            self.assertEqual(sample['probabilities'], self.req['probabilities'])

    def test_reverse_absorbing_direction(self):
        self.req['evolution'].update(rate_0_to_1_per_s=1., rate_1_to_0_per_s=0.)
        out = k.evolve_binary(self.req)
        self.assertGreater(out['samples'][-1]['probabilities'][-1], .98)

    def test_correlations_not_replaced_by_iid(self):
        self.req.update(block_length=2, probabilities=[.5, 0, 0, .5], queries=[])
        out = k.evolve_binary(self.req)
        self.assertEqual(out['samples'][0]['probabilities'], [.5, 0, 0, .5])
        p_b = .5 * math.exp(-.1)
        self.assertNotAlmostEqual(out['samples'][1]['probabilities'][3], p_b*p_b)

    def test_detailed_balance_equilibrium(self):
        a, b = 2., 3.
        self.req['probabilities'] = [(.4**i.bit_count())*(.6**(3-i.bit_count())) for i in range(8)]
        self.req['evolution'].update(rate_0_to_1_per_s=a, rate_1_to_0_per_s=b)
        out = k.evolve_binary(self.req)
        for sample in out['samples']:
            for x, y in zip(sample['probabilities'], self.req['probabilities']):
                self.assertAlmostEqual(x,y,delta=2e-15)

    def test_inputs_and_authority_detached(self):
        original = deepcopy(self.req)
        a, b = k.analyze(self.req), k.evolve_binary(self.req)
        a['alphabet'].append('X')
        a['authority']['state_admission'] = 'forged'
        b['model']['time_s'].append(9)
        self.assertEqual(self.req, original)
        self.assertEqual(k.AUTHORITY['state_admission'], 'not_performed')

    def test_no_implicit_renormalization(self):
        self.req['probabilities'] = [1/16] * 8
        with self.assertRaisesRegex(ValueError,'normalization'):
            k.analyze(self.req)

    def test_declared_source_not_physical_measurement(self):
        self.req['source_kind'] = 'declared_distribution'
        out = k.analyze(self.req)
        self.assertEqual(out['source_kind'], 'declared_distribution')
        self.assertEqual(out['authority']['source_authentication'], 'not_established')
        self.assertEqual(out['authority']['thermodynamic_entropy'], 'not_established')

    def test_array_and_depth_bounds(self):
        for field, value in [('queries', [['A']]*33), ('probabilities',[0.]*4097), ('block_length', 9)]:
            r = deepcopy(self.req); r[field]=value
            with self.assertRaises(ValueError): k.analyze(r)
        x=0
        for _ in range(20): x=[x]
        with self.assertRaises(ValueError): k.bounded_json(x)

    def test_no_evolution_model(self):
        self.req['evolution']=None
        with self.assertRaises(ValueError): k.evolve_binary(self.req)

    def test_positive_flux_underflow_refuses(self):
        self.req.update(block_length=1, probabilities=[1,1e-300], queries=[])
        self.req['evolution'].update(rate_0_to_1_per_s=0,rate_1_to_0_per_s=1e-100)
        with self.assertRaisesRegex(ValueError,'underflow'): k.evolve_binary(self.req)

    def test_seeded_marginal_and_context_oracles(self):
        for seed in range(40):
            req, counts = stationary(n=3, length=3, seed=seed)
            out=k.analyze(req)
            self.assertEqual(out['status'],'PASS')
            states=list(itertools.product(range(3), repeat=3))
            for context_index, context in enumerate(itertools.product(range(3), repeat=2)):
                exact_prefix=sum(Fraction(count,127) for state,count in zip(states,counts) if state[:-1]==context)
                exact_suffix=sum(Fraction(count,127) for state,count in zip(states,counts) if state[1:]==context)
                self.assertEqual(exact_prefix, exact_suffix)
                self.assertAlmostEqual(out['prefix_masses'][context_index],float(exact_prefix),delta=1e-15)
            # Edge list has the same target/source orientation as the upstream CTM.
            accumulated=[0.] * 9
            for e in out['transfer_edges']:
                accumulated[e['to_context']]+=out['prefix_masses'][e['from_context']]*e['probability']
            for x,y in zip(accumulated,out['suffix_masses']): self.assertAlmostEqual(x,y,delta=1e-15)

    def test_every_short_query_against_fraction_oracle(self):
        req,counts=stationary(n=2,length=4)
        req['queries']=[list(seq) for length in range(1,5) for seq in itertools.product(req['alphabet'],repeat=length)]
        out=k.analyze(req)
        words=list(itertools.product(req['alphabet'],repeat=4))
        for query in out['queries']:
            length=len(query['sequence'])
            oracle=sum(Fraction(c,127) for word,c in zip(words,counts) if list(word[:length])==query['sequence'])
            self.assertAlmostEqual(query['probability'],float(oracle),delta=1e-15)


for label,field,value in [
    ('nan','probabilities',[float('nan')]*8),('inf','probabilities',[float('inf')]*8),
    ('bool_probability','probabilities',[True]+[0.]*7),('negative','probabilities',[-.1]+[1.1/7]*7),
    ('alphabet_duplicate','alphabet',['A','A']),('alphabet_number','alphabet',[0,1]),
    ('alphabet_blank','alphabet',['A',' ']),('block_bool','block_length',True),
    ('block_zero','block_length',0),('block_float','block_length',3.),
    ('wrong_unit','unit','mol'),('wrong_schema','schema','v999'),('wrong_kind','source_kind','observed'),
    ('bad_hash','source_ref','sha256:bad'),('closure_integer','allow_markov_closure',1),
    ('bad_symbol','queries',[['X']]),('empty_query','queries',[[]]),('long_query','queries',[['A']*65]),
    ('bad_evolution','evolution',{})]:
    def invalid(self,field=field,value=value):
        self.req[field]=value
        with self.assertRaises(ValueError): k.analyze(self.req)
    setattr(SequenceTests,'test_reject_'+label,invalid)

for label,field,value in [
    ('negative_rate','rate_0_to_1_per_s',-1),('bool_rate','rate_0_to_1_per_s',True),
    ('rate_nan','rate_0_to_1_per_s',float('nan')),('huge_rate','rate_1_to_0_per_s',1e7),
    ('wrong_model','model','interacting_copolymerization'),('times_empty','time_s',[]),
    ('times_repeat','time_s',[0,1,1]),('times_reverse','time_s',[0,2,1]),
    ('times_no_zero','time_s',[1,2]),('times_negative','time_s',[-1,0]),
    ('times_bool','time_s',[False,1]),('rate_time_limit','time_s',[0,701]),
    ('times_budget','time_s',list(range(65)))]:
    def invalid(self,field=field,value=value):
        self.req['evolution'][field]=value
        with self.assertRaises(ValueError): k.evolve_binary(self.req)
    setattr(SequenceTests,'test_reject_'+label,invalid)


class WorkflowBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.req=k.example_request(); self.source=w.make_source(self.req)
        self.selection={'channel':'declaration','interval_s':[0.,1.], 'revision':0}

    def test_source_uses_existing_content_identity(self):
        self.assertEqual(self.source['evidence_id'],evidence_id(self.source))
        self.assertEqual(w.source_request(self.source),self.req)

    def test_runtime_hashes_real_source(self):
        runtime=w.runtime_identity()
        self.assertEqual(runtime['native_gambit'],'not_loaded')
        self.assertEqual(runtime['source_hashes']['sequence_kinetics.py'],hashlib.sha256(Path(k.__file__).read_bytes()).hexdigest())

    def test_real_operation_registry_descriptors(self):
        registry=OperationRegistry()
        for op in w.operations(): registry.register(op)
        self.assertEqual(registry.describe(),[{'operation_id':w.ANALYZE,'role':'backend'},{'operation_id':w.EVOLVE,'role':'backend'}])
        for op in w.operations():
            data=op.execute(self.source,{})
            w.validate_payload(op.operation_id,data,self.source,{},self.selection)

    def test_repeated_calls_share_evidence_not_result_objects(self):
        op=w.operations()[0]
        one,two=op.execute(self.source,{}),op.execute(self.source,{})
        self.assertEqual(one,two)
        one['prefix_masses'][0]=999
        self.assertNotEqual(one,two)

    def test_static_validation_does_not_reexecute(self):
        data=k.analyze(self.req)
        with patch.object(k,'analyze',side_effect=AssertionError('must not execute')):
            w.validate_payload(w.ANALYZE,data,self.source,{},self.selection)

    def test_static_evolution_validation_does_not_reexecute(self):
        data=k.evolve_binary(self.req)
        with patch.object(k,'evolve_binary',side_effect=AssertionError('must not execute')):
            w.validate_payload(w.EVOLVE,data,self.source,{},self.selection)

    def test_schema_check_not_false_numerical_verification(self):
        data=k.analyze(self.req)
        data['queries'][0]['probability']=.49
        # Within-domain coherent edits need NEW numerical verification; inspection is structural only.
        w.validate_payload(w.ANALYZE,data,self.source,{},self.selection)

    def test_source_change_changes_identity(self):
        self.req['source_ref']=content_identity({'different':1})
        self.assertNotEqual(w.make_source(self.req)['evidence_id'],self.source['evidence_id'])

    def test_alphabet_order_changes_identity(self):
        self.req['alphabet'].reverse()
        self.assertNotEqual(w.make_source(self.req)['evidence_id'],self.source['evidence_id'])

    def test_resealed_source_with_changed_transport_refuses(self):
        self.source['time_s']=[.5];self.source['evidence_id']=evidence_id(self.source)
        with self.assertRaises(ValueError): w.source_request(self.source)

    def test_no_saved_provider_loading(self):
        self.req['provider']='ctypes.CDLL'
        with self.assertRaises(ValueError): w.make_source(self.req)

    def test_bad_parameters_refuse(self):
        with self.assertRaises(ValueError): w.operations()[0].execute(self.source,{'engine':'gambit'})

    def test_wrong_selection_refuses(self):
        self.selection['interval_s']=[0.,.5]
        with self.assertRaises(ValueError): w.validate_payload(w.ANALYZE,k.analyze(self.req),self.source,{},self.selection)

    def test_authority_escalation_refuses(self):
        data=k.analyze(self.req);data['authority']['state_admission']='accepted'
        with self.assertRaises(ValueError): w.validate_payload(w.ANALYZE,data,self.source,{},self.selection)

    def test_wrong_request_binding_refuses(self):
        data=k.analyze(self.req);data['request_ref']=content_identity({})
        with self.assertRaises(ValueError): w.validate_payload(w.ANALYZE,data,self.source,{},self.selection)

    def test_missing_sample_refuses(self):
        data=k.evolve_binary(self.req);data['samples'].pop()
        with self.assertRaises(ValueError): w.validate_payload(w.EVOLVE,data,self.source,{},self.selection)


class JsonBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'request.json'

    def test_roundtrip(self):
        self.path.write_text(json.dumps(k.example_request()))
        self.assertEqual(load_request(self.path),k.example_request())

    def test_symlink(self):
        self.path.write_text(json.dumps(k.example_request()))
        link=self.path.with_name('link.json')
        try: link.symlink_to(self.path)
        except OSError: self.skipTest('Host does not permit test symlinks')
        with self.assertRaises(ValueError): load_request(link)

    def test_size(self):
        self.path.write_bytes(b' '*(k.MAX_BYTES+1))
        with self.assertRaises(ValueError): load_request(self.path)

for name,raw in [('duplicate','{"a":1,"a":2}'),('nonfinite','{"a":NaN}'),('underflow','{"a":1e-999}'),
                 ('infinity','{"a":1e999}'),('empty',''),('trailing','{} {}'),('wrong','[]')]:
    def invalid(self,raw=raw):
        self.path.write_text(raw)
        with self.assertRaises(ValueError): load_request(self.path)
    setattr(JsonBoundaryTests,'test_'+name,invalid)



class RetainedShapeTests(unittest.TestCase):
    def setUp(self):
        self.request=k.example_request()
        self.source=w.make_source(self.request)
        self.data=k.analyze(self.request)
        self.selection={'channel':'declaration','interval_s':[0.,1.], 'revision':0}

    def assert_refuses(self):
        with self.assertRaises(ValueError):
            w.validate_payload(w.ANALYZE,self.data,self.source,{},self.selection)

    def test_unknown_result_field(self):
        self.data['provider']='native';self.assert_refuses()
    def test_context_order(self):
        self.data['contexts'].reverse();self.assert_refuses()
    def test_edge_orientation(self):
        self.data['transfer_orientation']='reversed';self.assert_refuses()
    def test_edge_target(self):
        self.data['transfer_edges'][0]['to_context']=99;self.assert_refuses()
    def test_edge_duplicate(self):
        self.data['transfer_edges'][1]=self.data['transfer_edges'][0].copy();self.assert_refuses()
    def test_bad_entropy(self):
        self.data['block_entropy_nats']=-1;self.assert_refuses()
    def test_forged_closure(self):
        self.data['closure']['longer_sequences_authorized']=True;self.assert_refuses()
    def test_prediction_without_closure(self):
        self.data['queries'][2].update(status='PASS',probability=.125);self.assert_refuses()
    def test_expand_must_not_publish_probability(self):
        self.data['queries'][2]['probability']=.1;self.assert_refuses()
    def test_wrong_query_scope(self):
        self.data['queries'][0]['scope']='observed';self.assert_refuses()
    def test_marginal_status_inconsistent(self):
        self.data['max_prefix_suffix_difference']=.5;self.assert_refuses()
    def test_false_entropy_rate(self):
        self.data['markov_entropy_rate_nats_per_symbol']=2;self.assert_refuses()
    def test_underflow_requires_log_value(self):
        self.data['queries'][0].update(status='UNDERFLOW',probability=None,log_probability_nats=None);self.assert_refuses()
    def test_kinetic_rate_boolean_in_retained_model(self):
        data=k.evolve_binary(self.request)
        data['model']['rate_0_to_1_per_s']=False
        with self.assertRaises(ValueError):
            w.validate_payload(w.EVOLVE,data,self.source,{},self.selection)


if __name__=='__main__': unittest.main()
