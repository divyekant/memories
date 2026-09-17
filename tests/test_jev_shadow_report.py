from scripts.jev_shadow_report import summarize


def test_errors_and_unparseable_primary_do_not_inflate_agreement():
    rows=[
        {'call_id':'one','process_id':'a','status':'ok','fact_count':2,'primary_decisions':[{},{}], 'shadow_decisions':[{'target_valid':True},{'target_valid':False}], 'action_matches':2,'joint_matches':1,'latency_ms':20,'primary_latency_ms':100,'dropped_total':3},
        {'call_id':'bad','process_id':'a','status':'error','error':'HTTP429','dropped_total':5},
        {'call_id':'unpaired','status':'ok','fact_count':3,'primary_decisions':None,'shadow_decisions':[], 'primary_parse_error':True,'action_matches':None,'joint_matches':None},
    ]
    s=summarize(rows+[rows[0]])
    assert s['calls']==3
    assert s['paired_facts']==2
    assert s['action_matches']==2 and s['joint_matches']==1
    assert s['invalid_targets']==1
    assert s['status']=={'ok':2,'error':1}
    assert s['dropped_observed']==5
