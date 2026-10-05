"""Full-checkout NET integration gate. Requires real Session; no substitute host."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ciw import sequence_kinetics as kernel
from ciw import sequence_workflow as workflow
from ciw import sequence_cli
from ciw.core.records import validate_run_structure
from ciw.adapters.protocol import InstrumentManifest
from ciw.session import Session


class NativeSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.request=kernel.example_request()

    def test_source_uses_actual_native_run_and_manifest_contracts(self):
        source=workflow.make_source(self.request)
        validate_run_structure(source)
        InstrumentManifest.from_dict(source['metadata']['manifest'])

    def test_run_retains_two_native_execution_occurrences(self):
        result=workflow.run(self.request,self.root/'run')
        self.assertEqual(result['status'],'PASS')
        self.assertEqual(len(result['executions']),2)
        self.assertEqual(len(result['results']),2)
        self.assertEqual(len({r['execution_id'] for r in result['results']}),2)
        for r in result['results']:
            self.assertTrue(r['result_id'].startswith('result-'))
            self.assertNotEqual(r['execution_id'],r['result_id'])
            self.assertEqual(r['evidence_id'],result['evidence_id'])
            self.assertIsNone(r['verification_id'])
            self.assertEqual(r['verification_status'],'not_verified')

    def test_inspection_does_not_execute(self):
        first=workflow.run(self.request,self.root/'run')
        with patch.object(kernel,'analyze',side_effect=AssertionError('unexpected execution')), \
             patch.object(kernel,'evolve_binary',side_effect=AssertionError('unexpected execution')):
            read=workflow.inspect(self.root/'run')
        self.assertEqual(first,read)

    def test_replay_new_occurrences_same_evidence(self):
        first=workflow.run(self.request,self.root/'run')
        second=workflow.replay(self.root/'run',self.root/'replay')
        self.assertEqual(first['evidence_id'],second['evidence_id'])
        self.assertEqual([r['data'] for r in first['results']],[r['data'] for r in second['results']])
        self.assertFalse({r['execution_id'] for r in first['results']} & {r['execution_id'] for r in second['results']})
        self.assertFalse({r['result_id'] for r in first['results']} & {r['result_id'] for r in second['results']})

    def test_read_only_registration_without_bound_provider(self):
        workflow.run(self.request,self.root/'run')
        workflow.register_schemas()
        with tempfile.TemporaryDirectory() as out:
            session=Session.from_workspace(self.root/'run'/'workspace.json',Path(out))
        self.assertEqual(len(session.results),2)

    def test_incompatible_marginals_stop_before_evolution(self):
        self.request.update(block_length=2,probabilities=[0,1,0,0])
        result=workflow.run(self.request,self.root/'run')
        self.assertEqual(result['status'],'REFUSE')
        self.assertEqual(len(result['executions']),1)
        self.assertEqual(result['results'][0]['data']['status'],'REFUSE')

    def test_no_model_only_analysis(self):
        self.request['evolution']=None
        result=workflow.run(self.request,self.root/'run')
        self.assertEqual(len(result['executions']),1)

    def test_bad_request_creates_no_output(self):
        self.request['unit']='mol'
        with self.assertRaises(ValueError): workflow.run(self.request,self.root/'not-created')
        self.assertFalse((self.root/'not-created').exists())

    def test_create_only(self):
        workflow.run(self.request,self.root/'run')
        before=(self.root/'run'/'workspace.json').read_bytes()
        with self.assertRaises(FileExistsError): workflow.run(self.request,self.root/'run')
        self.assertEqual(before,(self.root/'run'/'workspace.json').read_bytes())

    def test_sidecar_change_refuses(self):
        workflow.run(self.request,self.root/'run')
        self.request['source_kind']='declared_distribution'
        (self.root/'run'/'request.json').write_text(json.dumps(self.request))
        with self.assertRaises(ValueError): workflow.inspect(self.root/'run')


if __name__=='__main__': unittest.main()
