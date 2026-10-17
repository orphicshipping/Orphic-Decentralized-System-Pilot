"""Acceptance regressions for failures found during the full order audit."""
from copy import deepcopy
from pathlib import Path

import pytest

from ciw.agent_api import encode, parse
from ciw.link_store import Journal
from ciw.operational_collection import Collector, link_segments
from ciw.operational_cli import example, default_context
from ciw.operational_agent import OperationalHost


@pytest.mark.parametrize('boundary', ['claim', 'segment', 'terminal'])
def test_stop_survives_concurrent_collector_save(tmp_path, boundary):
    with Journal(tmp_path/'journal.sqlite', create=True) as journal:
        collector = Collector(journal, 'reader', max_segments=3, cadence_s=0)
        save = collector._save
        injected = False
        def interleave(value):
            nonlocal injected
            selected = ((boundary == 'claim' and value.get('in_flight')) or
                        (boundary == 'segment' and value.get('segments') and not value.get('in_flight')) or
                        (boundary == 'terminal' and value.get('status') == 'LIMIT_CLOSED'))
            if not injected and selected:
                injected = True
                with Journal(journal.path) as operator:
                    Collector(operator, 'reader').request_stop()
            return save(value)
        collector._save = interleave
        state = collector.run(lambda i: b'x', source_identity={'adapter': 'inert'})
        assert injected and state['stop_requested'] is True
        assert collector.state() == state
        if boundary != 'terminal':
            assert state['status'] == 'STOPPED'
            assert len(state['segments']) <= 1


@pytest.mark.parametrize('chunk_size', [1, 10, 65536])
def test_link_fragmentation_rotates_without_cumulative_duplicates(tmp_path, chunk_size):
    from ciw.link_profiles import example_manifest, example_observations
    from ciw.link_protocol import encode as wire
    from ciw.link_gateway import validate_capture
    device = example_manifest()
    observations = example_observations(device, rounds=2)
    stream = wire('manifest', encode(device)) + b''.join(wire('observation', encode(v)) for v in observations)
    chunks = [stream[i:i+chunk_size] for i in range(0, len(stream), chunk_size)]
    with Journal(tmp_path/'journal.sqlite', create=True) as journal:
        captures = [parse(raw) for raw in link_segments(chunks, journal, expected_manifest=device,
                                                      boot_id='synthetic.boot.1', max_records=2)]
        assert all(0 < len(v['frames']) <= 2 for v in captures)
        observed = [r for v in captures for r in validate_capture(v)['observations']]
        assert observed == observations


def test_link_incomplete_tail_retains_closed_prefix(tmp_path):
    from ciw.link_profiles import example_manifest, example_observations
    from ciw.link_protocol import encode as wire
    device = example_manifest(); observations = example_observations(device, rounds=1)
    stream = wire('manifest', encode(device)) + wire('observation', encode(observations[0]))
    with Journal(tmp_path/'journal.sqlite', create=True) as journal:
        iterator = link_segments([stream, b'NLK'], journal, expected_manifest=device, boot_id='synthetic.boot.1')
        assert len(parse(next(iterator))['frames']) == 1
        with pytest.raises(ValueError, match='Incomplete'):
            next(iterator)
        assert len(journal.rows()) == 2


def test_generated_configuration_uses_isolated_installed_interpreter(tmp_path):
    example(tmp_path)
    conf = parse(next(tmp_path.glob('mcp-*.json')).read_bytes())['mcpServers']['ncp-operational']
    assert conf['args'][:3] == ['-I', '-m', 'ciw.net']


def test_cli_collects_fragmented_link_input_with_selected_rotation(tmp_path,capsys):
    from ciw.link_profiles import example_manifest,example_observations
    from ciw.link_protocol import encode as wire
    from ciw.operational_cli import main
    device=example_manifest();observations=example_observations(device,rounds=1)
    stream=wire('manifest',encode(device))+b''.join(wire('observation',encode(row)) for row in observations)
    (tmp_path/'manifest.json').write_bytes(encode(device))
    (tmp_path/'a.bin').write_bytes(stream[:10]);(tmp_path/'b.bin').write_bytes(stream[10:])
    conf={'schema':'ncp.operational-collector-profile.v1','journal':'link.sqlite','id':'reader',
        'adapter':'link-receive','binding':{'paths':['a.bin','b.bin'],'manifest':'manifest.json',
            'boot_id':'synthetic.boot.1','segment_records':2},'max_segments':64,'max_bytes':1000000,'cadence_s':0}
    path=tmp_path/'collector.json';path.write_bytes(encode(conf))
    assert main(['collect','--manifest',str(path)])==0
    result=parse(capsys.readouterr().out.encode())
    assert result['status']=='CLOSED' and len(result['segments'])==(len(observations)+1)//2


def test_simulated_observations_keep_native_origin_under_recorded_context():
    from ciw.reference_comparison import example_request
    from ciw.operational_context import make_binding, rows
    raw = encode(example_request()['right'])
    binding = make_binding(raw, 'observations', default_context(clock='synthetic.clock', origin='recorded'), [])
    row = rows(raw, binding)[0]
    assert row['origin'] == 'synthetic'
    assert row['recording_origin'] == 'recorded'
    assert row['lineage']['provenance']['semantics'] == 'simulated'


def test_unknown_time_rows_are_discoverable_and_traceable_without_dispatch(tmp_path, monkeypatch):
    from ciw.operational_cli import prepare
    raw = (Path(__file__).parent/'fixtures/operational/egm-synthetic-study.json').read_bytes()
    profile = prepare(tmp_path, investigation_id='unknown-time', sources={'study': {
        'raw': raw, 'family': 'elapsed-gauge-study', 'context': default_context(origin='synthetic'), 'configurations': []}})
    with OperationalHost(profile) as host:
        catalog = host.call('oe_catalog', {})
        assert catalog['sources']['study']['unplaced_count'] > 0
        result = host.call('oe_rows', {'source': 'study', 'unplaced_only': True, 'limit': 1})
        row = result['rows'][0]
        assert row['time'] is None
        traced = host.call('oe_trace', {'source': 'study', 'observation': row['id']})
        assert traced['observation'] == row
        assert not host.agent._attempts


def test_existing_scalar_workflow_executes_reopens_and_keeps_shared_covariance(tmp_path, monkeypatch):
    from ciw.link_study_cli import example as scalar_example
    from ciw.link_study import OPS
    from ciw.operational_cli import prepare
    request=scalar_example(); capture=request['capture']
    profile=prepare(tmp_path,investigation_id='native-scalar',sources={'capture':{
        'raw':encode(capture),'family':'link-capture','context':default_context(
            producer=capture['manifest']['device_id'],clock=capture['manifest']['clock']['id'],boot=capture['boot_id'],origin='synthetic'),
        'configurations':[]}},models={'scalar':{'source':'capture','engine':'scalar','request':encode(request),
            'policy':{'unit':'N','coverage_factor':2,'max_abs_error':.1,'max_standard_uncertainty':.1}}})
    with OperationalHost(profile) as host:
        run=host.call('net_execute',{'source':'scalar','graph':'scalar-graph','attempt':'native-first'})
        assert run['status']=='completed',run
        finding=host.call('oe_model_support',{'attempt':'native-first'})
        assert finding['data']['status']=='INSUFFICIENT_EVIDENCE'
        summary=finding['data']['model']['native_results']
        assert summary[OPS[2]]['mean']==pytest.approx(2.03)
        assert summary[OPS[2]]['variance']==pytest.approx(.00190808)
        assert summary[OPS[3]]['status']=='PASS'
    from ciw import operational_native
    monkeypatch.setattr(operational_native,'execute',lambda *a:pytest.fail('Retained inspection dispatched science'))
    with OperationalHost(profile,resume=True) as host:
        assert host.call('oe_model_support',{'attempt':'native-first'})==finding
        assert host.call('net_execute',{'source':'scalar','graph':'scalar-graph','attempt':'native-first'})['reused_response']


def test_calibrated_window_adapter_refuses_unbound_provider_and_source_substitution(tmp_path):
    from ciw.operational_cli import prepare
    raw=(Path(__file__).parents[1]/'examples/calibrated-window/source.json').read_bytes()
    native=parse(raw)
    source={'raw':raw,'family':'calibrated-window','context':default_context(clock=native['device_clock']['clock_id']),
            'configurations':[]}
    model={'source':'window','engine':'calibrated-window','request':None,
        'policy':{'unit':'m','coverage_factor':2,'max_abs_error':.1,'max_standard_uncertainty':.1}}
    profile=prepare(tmp_path/'bound',investigation_id='native-window',sources={'window':source},models={'window-model':model})
    with OperationalHost(profile) as host:
        result=host.call('net_execute',{'source':'window-model','graph':'window-model-graph','attempt':'missing-stack'})
        assert result['status']!='completed'
        assert host.call('oe_model_support',{'attempt':'missing-stack'})['data']['status']=='INSUFFICIENT_EVIDENCE'
    altered=parse(raw);altered['samples'][0]['indicated_value']+=.1
    with pytest.raises(ValueError,match='substitution'):
        prepare(tmp_path/'substitution',investigation_id='invalid',sources={'window':source},
            models={'window-model':{**model,'request':encode(altered)}})


def test_hypotheses_and_proposals_bind_each_changed_field_and_actual_policy(tmp_path):
    from ciw.operational_cli import prepare,configuration,default_template
    from ciw.reference_comparison import example_request
    request=example_request()
    profile=prepare(tmp_path,investigation_id='hypotheses',sources={'observed':{
        'raw':encode(request['right']),'family':'observations',
        'context':default_context(clock='synthetic.clock',origin='synthetic'),
        'configurations':[configuration('a',{'power':10,'fan':1},clock='synthetic.clock'),
            configuration('b',{'power':20,'fan':2},clock='synthetic.clock',effective=(.5,.5))]}},
        models={'model':{'source':'observed','engine':'reference','request':encode(request),'policy':{}}},
        templates={'resolve':default_template()})
    with OperationalHost(profile) as host:
        selection={'source':'observed','clock':'synthetic.clock','boot':'boot-1','channel':'temperature','start':0,'end':.5}
        before=host.call('oe_query',selection)
        after=host.call('oe_query',{**selection,'start':.5,'end':2})
        changed=host.call('oe_change',{'baseline':before['record'],'changed':after['record']})
        hypotheses=changed['data']['hypotheses']
        assert {h['variable'] for h in hypotheses if h['kind']=='configuration_response'}=={'/power','/fan'}
        assert all(before['record'] in h['evidence'] for h in hypotheses)
        proposal=host.call('oe_propose',{'finding':changed['record'],'template':'resolve'})
        tests=proposal['data']['discrimination']['hypothesis_tests']
        assert {t['hypothesis_id'] for t in tests}=={h['hypothesis_id'] for h in hypotheses}
        assert all(t['quantitative_prediction'] is None and t['prediction_limit'] for t in tests)
        assert proposal['data']['discrimination']['information_gain']=='not_quantified'
        host.call('net_execute',{'source':'model','graph':'model-graph','attempt':'policy'})
        model=host.call('oe_model_support',{'attempt':'policy'})
        prop=host.call('oe_propose',{'finding':model['record'],'template':'resolve'})
        assert all(t['decision_targets']==request['policy'] for t in prop['data']['discrimination']['hypothesis_tests'])
