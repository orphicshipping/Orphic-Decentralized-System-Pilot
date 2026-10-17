"""Read-only connective projection of EGM research-study/1 declarations.

No EGM parser/generator/model/ratio implementation is replaced or dispatched.
Full native EGM validation is a separate upstream operation. Unknown acquisition
timestamps stay unknown; supplied durations are not timestamps or logical ticks.
"""
from .agent_api import identifier
from .control_contracts import keys,number,text
from .core.identities import content_identity
from .operational_context import _row

SCHEMA='microgauge-research-study/1'
UPSTREAM='169bdcd8057b80abda4670a1d9133be54c7f8fc5'


def project(value):
    keys(value,{'schema','studyId','generation','candidates','observations'})
    if value['schema']!=SCHEMA:raise ValueError('Unsupported EGM study schema')
    identifier(value['studyId'])
    if type(value['candidates']) is not list or not 1<=len(value['candidates'])<=16:
        raise ValueError('Bounded EGM candidate declarations required')
    if type(value['observations']) is not list or len(value['observations'])>64:
        raise ValueError('Bounded EGM supplied observations required')
    candidates={}
    for candidate in value['candidates']:
        keys(candidate,{'candidateId','kind','resourceId','workloadId','hypothesis','factor','plannedRepetitions','syntheticTrace'})
        name=identifier(candidate['candidateId'])
        if name in candidates or candidate['kind']!='proposal':raise ValueError('Ambiguous EGM candidate')
        candidates[name]=candidate
    seen=set();rows=[]
    for observation in value['observations']:
        keys(observation,{'observationId','candidateId','arm','resource','workload','configurationId','provenance',
                          'work','duration','energy','acquisitionCost'})
        identity=identifier(observation['observationId'])
        if identity in seen or observation['candidateId'] not in candidates:raise ValueError('Invalid EGM observation/candidate identity')
        seen.add(identity)
        p=observation['provenance'];keys(p,{'kind','originKind','sourceId','description'})
        if p['kind'] not in {'observed','replay','synthetic'}:raise ValueError('Unknown EGM provenance assertion')
        if p['kind']=='replay' and p['originKind'] not in {'observed','synthetic','unknown'}:raise ValueError('Replay origin required')
        if p['kind']!='replay' and p['originKind'] is not None:raise ValueError('Unexpected EGM replay origin')
        for name in ('sourceId','description'):text(p[name])
        if observation['arm'] not in {'baseline','candidate'}:raise ValueError('Unknown asserted study arm')
        for field in ('work','duration','energy'):
            item=observation[field]
            if item is None:
                val=None;unit='unknown'
            else:
                fields={'work':{'value','unit','definition','scopeId'},'duration':{'value','unit','basis','scopeId'},
                        'energy':{'value','unit','boundary','scopeId'}}[field]
                keys(item,fields);val=number(item['value']);unit=item['unit'];text(unit)
                if val<0:raise ValueError('Negative EGM scalar declaration')
            rows.append(_row(content_identity([value['studyId'],identity,field]),field,field,unit,'declared-study-interval',None,val,
                origin='synthetic' if p['kind']=='synthetic' else 'synthetic_origin_replay' if p['originKind']=='synthetic' else 'unverified_'+p['kind'],
                lineage={'upstream_revision':UPSTREAM,'native_observation':observation,
                    'candidate':candidates[observation['candidateId']], 'configuration_id':observation['configurationId'],
                    'timestamp':'not_supplied','native_validation':'not_performed; structural_projection_only',
                    'cross_record_comparison':'unsupported_by_upstream_contract','hardware_execution':'not_performed'}))
    return rows
