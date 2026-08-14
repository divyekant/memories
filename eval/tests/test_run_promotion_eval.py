"""Contract tests for the provider-backed promotion fixture gate."""
import importlib.util, json
from pathlib import Path

EVAL_PATH=Path(__file__).resolve().parents[1]/"run_promotion_eval.py"
FIXTURE_PATH=Path(__file__).resolve().parents[1]/"fixtures"/"project_promotion_v1.jsonl"
def module():
    spec=importlib.util.spec_from_file_location("promotion_eval",EVAL_PATH); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

class FakeRunner:
    provider="fake-extract"; model="extract-v1"; reviewer_provider="fake-review"; reviewer_model="review-v1"; policy="classifier-v1|reviewer-v1"
    def __init__(self, overrides=None): self.overrides=overrides or {}; self.calls=[]
    def predict(self,row):
        self.calls.append(row["id"])
        expected=row["expected_visibility"]=="project" and row["expected_review"]=="approve"
        value={"visibility":"project" if expected else "private","kind":row["expected_kind"] if expected else None,"decision":row["expected_review"],"route":"ordinary" if expected else "not_routed","relevance":.92 if expected else .1,"promoted":expected,"provider":self.provider,"model":self.model,"reviewer_provider":self.reviewer_provider,"reviewer_model":self.reviewer_model,"policy":self.policy}
        value.update(self.overrides.get(row["id"],{})); return value

def row(case_id="x",positive=True,weight=1,principal="dk",risk="confirmed_project_fact"):
    return {"id":case_id,"risk_class":risk,"conversation":{"principal":principal,"messages":[{"role":"user","content":"A dirty project claim."}],"controls":{}},"expected_visibility":"project" if positive else "private","expected_kind":"knowledge" if positive else None,"expected_review":"approve" if positive else "reject","high_risk":not positive,"weight":weight}

def test_runner_is_required_and_prefilled_answers_are_rejected():
    m=module(); assert "runner_required" in m.evaluate([row()],None)["failure_codes"]
    bad=row(); bad["conversation"]["messages"].append({"role":"assistant","content":json.dumps({"visibility":"project","review":"approve"})})
    report=m.evaluate([bad],FakeRunner()); assert "invalid_fixture" in report["failure_codes"]

def test_gate_enforces_weight_precision_recall_kind_review_and_versions():
    m=module(); rows=[row(str(i),True) for i in range(100)]
    assert m.evaluate(rows,FakeRunner())["gate_passed"] is False  # incomplete risk corpus
    overrides={"0":{"kind":"decisions"},"1":{"decision":"defer","promoted":False},"2":{"provider":None}}
    report=m.evaluate(rows,FakeRunner(overrides)); assert report["wrong_kind_count"]==1; assert report["wrong_review_count"]>=1; assert "version_identity_mismatch" in report["failure_codes"]

def test_fixture_corpus_is_unique_dirty_and_covers_both_principals_per_risk():
    m=module(); rows=m.load_fixtures(FIXTURE_PATH); assert len(rows)>=100; assert len({r["id"] for r in rows})==len(rows); assert sum(float(r["weight"]) for r in rows)>=100
    coverage={risk:set() for risk in m.RISKS}
    for r in rows:
        principal,messages,_=m.conversation_parts(r["conversation"]); coverage.setdefault(r["risk_class"],set()).add(principal)
        assert not any("provider_version" in x["content"] or '"visibility"' in x["content"] for x in messages)
    assert all(coverage[risk]==m.PRINCIPALS for risk in m.RISKS)

def test_full_fixture_gate_uses_runner_and_reports_raw_and_weighted_counts_without_text():
    m=module(); rows=m.load_fixtures(FIXTURE_PATH); runner=FakeRunner(); report=m.evaluate(rows,runner)
    assert report["gate_passed"] is True; assert len(runner.calls)==len(rows); assert isinstance(report["route_counts"]["ordinary"],int); assert "route_weights" in report
    rendered=json.dumps(report); assert "FPLGuru imports" not in rendered; assert "evidence_fingerprint" not in rendered

def test_duplicate_ids_and_incomplete_principal_coverage_fail():
    m=module(); rows=[row("dup"),row("dup",principal="darshan")]; report=m.evaluate(rows,FakeRunner())
    assert "invalid_fixture" in report["failure_codes"]; assert "incomplete_principal_risk_coverage" in report["failure_codes"]

def test_routing_rates_are_reported():
    m=module(); report=m.evaluate([row("route")],FakeRunner()); assert set(report["routing_rates"])>={"0.30","0.40","0.50","0.70"}; assert report["routing_rates"]["0.70"]==1.0
