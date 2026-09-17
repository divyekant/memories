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


def test_independent_events_join_and_missing_flows_are_explicit():
    shadow={'call_id':'pair','flow':'audn','event':'shadow','status':'ok','fact_count':1,
            'shadow_decisions':[{'action':'ADD','target':None,'target_valid':True}]}
    primary={'call_id':'pair','flow':'audn','event':'primary','status':'ok','baseline':{'status':'ok'},
             'primary_decisions':[{'action':'ADD','target':None,'target_valid':True}], 'primary_latency_ms':40}
    failed={'call_id':'failure','flow':'audn','event':'primary','status':'ok','baseline':{'status':'error'}}
    retrieval={'call_id':'search','flow':'retrieval','event':'combined','status':'ok',
               'total_count':50,'evaluated_count':20,'shadow_answers':{'intent':{'choice':'lookup'}}}
    result=summarize([primary,shadow,primary,failed,retrieval])
    assert result['paired_calls']==1 and result['joint_matches']==1
    assert result['flows']['audn']['missing_shadow']==1
    assert result['flows']['retrieval']['evaluated_count']==20
    assert result['flows']['pruning']['coverage']=='not_observed'
    assert result['flows']['retrieval']['answer_choices']=={'lookup':1}


def test_joint_agreement_requires_same_valid_target():
    primary={'call_id':'different','flow':'audn','event':'primary','status':'ok',
             'primary_decisions':[{'action':'UPDATE','target':1,'target_valid':True}]}
    shadow={'call_id':'different','flow':'audn','event':'shadow','status':'ok','fact_count':1,
            'shadow_decisions':[{'action':'UPDATE','target':2,'target_valid':True}]}
    result=summarize([primary,shadow])
    assert result['action_matches']==1 and result['joint_matches']==0
    primary['primary_decisions'][0].update(target=2,target_valid=False)
    assert summarize([primary,shadow])['joint_matches']==0
