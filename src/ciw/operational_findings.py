"""Deterministic evidence-specific hypotheses and discriminating test plans.

These are untested explanations. Quantitative predictions are never invented
when the selected native model does not map a proposed intervention.
"""
from copy import deepcopy
from .core.identities import content_identity


def hypotheses(finding):
    evidence=finding.get('parents',[])+finding.get('binding_dependencies',[])
    if finding.get('result_id'): evidence += [finding['result_id'],finding['comparison_ref']]
    values=[]
    diff=finding.get('configuration_diff')
    if diff:
        for changed in diff['fields']:
            if changed['status']=='unchanged': continue
            values.append({'kind':'configuration_response','variable':changed['field'],
                'contrast':{'baseline':changed['left'],'changed':changed['right']},
                'observation':deepcopy(finding['response']),
                'evidence':evidence,'confounders':[p for p in diff['confounders'] if p!=changed['field']],
                'distinguishing_evidence':'A separately admitted matched comparison varying this field while holding the other named configuration fields and source conditions fixed.',
                'quantitative_prediction':None,'prediction_limit':'The selected comparison does not predict the intervention response.'})
    reason=finding.get('outcome',{}).get('reason','configuration association does not identify cause')
    for kind,observable,distinguishing in (
        ('measurement_calibration','independent reference residual at the same quantity/frame/time',
         'Agreement of the independent reference with one sensor while another disagrees would distinguish their measurement chains; shared calibration dependencies must remain in the joint budget.'),
        ('input_timing','measured input history and clock alignment uncertainty',
         'A predeclared model comparison using independently established input timing can test whether timing accounts for the retained residual; fitting the same comparison points does not provide held-out validation.'),
        ('model_applicability','held-out response residual under unchanged model/policy',
         'Disagreement persisting after reference, timing and configuration applicability checks supplies evidence against the declared model within that domain; it does not uniquely identify a physical fault.')):
        values.append({'kind':kind,'variable':observable,'evidence':evidence,
            'observed_comparison':deepcopy(finding.get('outcome',finding.get('response'))),
            'evidence_gap':reason,'distinguishing_evidence':distinguishing,
            'quantitative_prediction':None,
            'prediction_limit':'No independent reference or candidate intervention prediction is supplied by this finding.'})
    total=len(values)
    for value in values:
        value['status']='UNTESTED_HYPOTHESIS'
        value['hypothesis_id']=content_identity(value)
    return {'hypotheses':values[:16],'total':total,'omitted':max(0,total-16),
            'causal_identification':'not_established'}


def plan(finding_ref, finding, template):
    structured=finding.get('hypotheses') or hypotheses(finding)['hypotheses']
    return {'finding':finding_ref,'hypothesis_tests':[{
        'hypothesis_id':h['hypothesis_id'],'kind':h['kind'],'source_evidence':h['evidence'],
        'candidate_settings':deepcopy(template['settings']),
        'controlled':deepcopy(template['controlled']),
        'observed':list(dict.fromkeys([h['variable'],*template['observed']])),
        'expected_distinguishing_evidence':h['distinguishing_evidence'],
        'quantitative_prediction':h['quantitative_prediction'],'prediction_limit':h['prediction_limit'],
        'decision_targets':deepcopy(finding.get('policy',{'status':'operator_template_only','rule':template['decision_rule']})),
        'required_new_evidence':True} for h in structured],
        'procedure':deepcopy(template['procedure']),'prerequisites':deepcopy(template['prerequisites']),
        'resource_limits':deepcopy(template['limits']),'required_grants':deepcopy(template['required_grants']),
        'operation_kind':template['kind'],'decision_rule':template['decision_rule'],
        'readiness':'HELD_NEW_EVIDENCE_AND_APPLICABILITY_REVIEW',
        'information_gain':'not_quantified','execution':'not_performed','approval':'not_granted'}
