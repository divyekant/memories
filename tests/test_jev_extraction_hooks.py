import json
from llm_provider import CompletionResult
import llm_extract as le


class Primary:
    model='primary';provider_name='test';supports_audn=True
    def __init__(self,text): self.text=text
    def complete(self,*args,**kwargs): return CompletionResult(self.text,2,3)


class Candidates:
    def hybrid_search(self,*args,**kwargs): return []


def test_fact_quality_observes_normalized_facts_without_changing_them(monkeypatch):
    seen=[]
    monkeypatch.setattr(le,'observe_jev',lambda *args,**kw:seen.append((args,kw)))
    result=le.extract_facts(Primary('[{"text":"We chose SQLite.","category":"decision"}]'),'user: We chose SQLite.',source='codex/test')
    assert result==[{'text':'We chose SQLite.','category':'decision'}]
    assert seen[0][0][0]=='extraction'
    assert seen[0][0][1]['conversation']=='user: We chose SQLite.'
    assert seen[0][0][1]['facts']==result


def test_audn_starts_shadow_before_primary_and_records_failure(monkeypatch):
    events=[]
    monkeypatch.setattr(le,'start_jev',lambda *args,**kw:events.append('shadow') or {'ticket':1})
    monkeypatch.setattr(le,'finish_jev',lambda ticket,baseline:events.append(baseline['status']))
    class Failed(Primary):
        def complete(self,*args,**kwargs):
            assert events==['shadow']
            events.append('primary')
            raise RuntimeError('unavailable')
    decisions,_,_=le.run_audn(Failed(''),Candidates(),[{'text':'fact'}],'codex/test')
    assert events==['shadow','primary','error']
    assert decisions==[{'action':'FALLBACK_ADD','fact_index':0}]


def test_relationship_review_uses_only_eligible_targets_and_preserves_links(monkeypatch):
    seen=[];writes=[]
    monkeypatch.setattr(le,'observe_jev',lambda *args,**kw:seen.append(args))
    class Engine:
        def add_link(self,*args): writes.append(args)
    similar={0:[{'id':2,'text':'related fact','source':'codex/test','rrf_score':0.9}, {'id':3,'text':'private other','source':'other/test','rrf_score':1.0}]}
    result=le._apply_maintenance(Engine(),[{'action':'ADD','fact_index':0}],{'actions':[{'action':'add','id':1}]},{'similar_per_fact':similar},source='codex/test',facts=[{'text':'new fact'}])
    assert writes==[(1,2,'related_to')]
    assert result['links_created'][0]['to_id']==2
    state=seen[0][1]
    assert state['pairs'][0]['from_memory']['text']=='new fact'
    assert state['pairs'][0]['to_memory']['id']==2
    assert 'private other' not in json.dumps(state)
