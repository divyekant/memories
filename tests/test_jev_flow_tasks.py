import json

import httpx
import pytest

from jev_shadow import JevShadow


@pytest.fixture(autouse=True)
def fake_typesafe_key(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")


def with_transport(handler):
    shadow = JevShadow()
    shadow._client = httpx.Client(transport=httpx.MockTransport(handler))
    return shadow


def close_client(shadow):
    shadow._client.close()


def choice(choice_name, criteria):
    probabilities = {key: 0.0 for key in criteria}
    probabilities[choice_name] = 1.0
    return {
        "type": "choice",
        "choice": choice_name,
        "probabilities": probabilities,
        "confidence": 0.8,
    }


def response_for(request, choices=None):
    payload = json.loads(request.content)
    choices = choices or {}
    answers = {
        key: choice(choices.get(key, next(iter(question["criteria"]))), question["criteria"])
        for key, question in payload["questions"].items()
    }
    return {
        "model": "jev-1.13.0",
        "answers": answers,
        "usage": {"input_tokens": 21, "output_tokens": 13},
    }


def test_extraction_evaluation_keeps_primary_categories_local():
    seen = []
    state = {
        "system": "memory extraction rules",
        "conversation": [{"speaker": "user", "text": "The release is stable."}],
        "facts": [
            {
                "text": "The release is stable.",
                "category": "decision",
                "speaker": "user",
                "condition": "after the final check",
                "visibility": "project",
                "project_kind": "knowledge",
            }
        ],
        "variant": "two_call",
    }
    baseline = {"facts": [{"text": state["facts"][0]["text"], "category": "decision"}], "oracle": "hidden"}

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json=response_for(
                request,
                {
                    "support_0": "supported",
                    "durability_0": "durable",
                    "category_0": "decision",
                },
            ),
        )

    shadow = with_transport(handler)
    try:
        result = shadow.evaluate("extraction", state, baseline)
    finally:
        close_client(shadow)

    assert result["schema_version"] == 2
    assert result["status"] == "ok"
    assert result["flow"] == "extraction"
    assert result["requested_model"] == "jev-latest"
    assert result["served_model"] == "jev-1.13.0"
    assert result["shadow_answers"]["support_0"]["choice"] == "supported"
    assert result["shadow_input_tokens"] == 21
    assert result["shadow_output_tokens"] == 13
    assert result["state"]["facts"][0] == {
        "index": 0,
        "text": "The release is stable.",
        "speaker": "user",
        "condition": "after the final check",
    }
    assert result["baseline"] == baseline

    payload = json.loads(seen[0].content)
    assert set(payload) == {"model", "state", "questions"}
    assert "baseline" not in payload
    assert "category" not in payload["state"]["facts"][0]
    assert "visibility" not in payload["state"]["facts"][0]
    assert "project_kind" not in payload["state"]["facts"][0]
    assert set(payload["questions"]) == {"support_0", "durability_0", "category_0"}


@pytest.mark.parametrize(
    ("flow", "state", "question_keys"),
    [
        (
            "relationships",
            {
                "pairs": [
                    {
                        "from_memory": {"id": 1, "text": "A", "source": "p"},
                        "to_memory": {"id": 2, "text": "B", "source": "p"},
                        "proposed_type": "related_to",
                    }
                ]
            },
            {"relation_0", "direction_0"},
        ),
        (
            "retrieval",
            {
                "query": "release status",
                "candidates": [{"id": 4, "text": "The release is stable."}],
                "context": {"route": "memory_search"},
            },
            {"relevance_0", "temporal_fit_0", "query_intent", "best_candidate"},
        ),
        (
            "consolidation",
            {
                "phase": "before_merge",
                "memories": [{"id": 1, "text": "A"}, {"id": 2, "text": "B"}],
                "proposed_texts": ["A and B"],
            },
            {"compatibility"},
        ),
        (
            "pruning",
            {"candidates": [{"id": 3, "text": "old"}], "rules": {"keep": "useful"}},
            {"disposition_0"},
        ),
        (
            "promotion",
            {"system": "review rules", "user": "candidate and evidence"},
            {"decision", "support", "shareability"},
        ),
    ],
)
def test_flow_evaluation_uses_common_validated_transport(flow, state, question_keys):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=response_for(request))

    shadow = with_transport(handler)
    try:
        result = shadow.evaluate(flow, state, {"status": "pending", "primary": "private"})
    finally:
        close_client(shadow)

    assert result["schema_version"] == 2
    assert result["status"] == "ok"
    assert result["flow"] == flow
    assert set(result["shadow_answers"]) == question_keys
    payload = json.loads(seen[0].content)
    assert "baseline" not in json.dumps(payload)


def test_retrieval_with_no_candidates_still_tests_intent_and_none_choice():
    seen = []
    state = {"query": "release status", "candidates": [], "context": {"route": "search"}}

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=response_for(request, {"query_intent": "lookup", "best_candidate": "none"}))

    shadow = with_transport(handler)
    try:
        result = shadow.evaluate("retrieval", state, {"ranking_ids": []})
    finally:
        close_client(shadow)

    assert result["status"] == "ok"
    assert result["evaluated_count"] == 0
    assert result["total_count"] == 0
    assert result["shadow_answers"]["best_candidate"]["choice"] == "none"
    payload = json.loads(seen[0].content)
    assert set(payload["questions"]) == {"query_intent", "best_candidate"}
    assert set(payload["questions"]["best_candidate"]["criteria"]) == {"none"}


def test_relationship_evaluation_strips_primary_proposed_type():
    seen = []
    state = {
        "pairs": [
            {
                "from_memory": {"id": 1, "text": "A"},
                "to_memory": {"id": 2, "text": "B"},
                "proposed_type": "related_to",
            }
        ]
    }

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=response_for(request))

    shadow = with_transport(handler)
    try:
        result = shadow.evaluate("relationships", state, {"proposed_type": "related_to"})
    finally:
        close_client(shadow)

    assert result["status"] == "ok"
    payload = json.loads(seen[0].content)
    assert "proposed_type" not in payload["state"]["pairs"][0]


def test_extraction_caps_facts_without_silent_truncation():
    seen = []
    facts = [{"text": f"fact {index}", "category": "detail"} for index in range(31)]
    state = {"system": "rules", "conversation": "conversation", "facts": facts, "variant": "two_call"}

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=response_for(request))

    shadow = with_transport(handler)
    try:
        result = shadow.evaluate("extraction", state, {"facts": facts})
    finally:
        close_client(shadow)

    assert result["status"] == "ok"
    assert result["evaluated_count"] == 30
    assert result["total_count"] == 31
    payload = json.loads(seen[0].content)
    assert len(payload["state"]["facts"]) == 30
    assert len(payload["questions"]) == 90


def test_pair_and_candidate_caps_use_declared_total_counts():
    seen = []
    pairs = [
        {"from_memory": {"id": index}, "to_memory": {"id": index + 100}}
        for index in range(20)
    ]
    candidates = [{"id": index, "text": f"fact {index}"} for index in range(20)]

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=response_for(request))

    shadow = with_transport(handler)
    try:
        relationships = shadow.evaluate(
            "relationships", {"pairs": pairs, "total_pairs": 27}, {}
        )
        retrieval = shadow.evaluate(
            "retrieval",
            {"query": "facts", "candidates": candidates, "total_candidates": 28},
            {},
        )
    finally:
        close_client(shadow)

    assert relationships["evaluated_count"] == 20
    assert relationships["total_count"] == 27
    assert retrieval["evaluated_count"] == 20
    assert retrieval["total_count"] == 28
    assert len(seen) == 2


def test_single_call_uses_four_questions_per_fact_and_caps_at_25():
    seen = []
    facts = [{"text": f"fact {index}", "category": "detail"} for index in range(26)]
    state = {"system": "rules", "conversation": "conversation", "facts": facts, "variant": "single_call"}

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=response_for(request))

    shadow = with_transport(handler)
    try:
        result = shadow.evaluate("extraction", state, {"facts": facts})
    finally:
        close_client(shadow)

    assert result["status"] == "ok"
    assert result["evaluated_count"] == 25
    assert result["total_count"] == 26
    payload = json.loads(seen[0].content)
    assert len(payload["state"]["facts"]) == 25
    assert len(payload["questions"]) == 100
    assert {key.rsplit("_", 1)[0] for key in payload["questions"] if key.endswith("_0")} == {
        "support",
        "durability",
        "category",
        "storage",
    }


def test_secret_in_baseline_skips_before_network_and_is_not_retained():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=response_for(request))

    shadow = with_transport(handler)
    try:
        result = shadow.evaluate(
            "promotion",
            {"system": "rules", "user": "candidate"},
            {"primary": "sk-12345678901234567890"},
        )
    finally:
        close_client(shadow)

    assert calls == []
    assert result["status"] == "skipped"
    assert result["reason"] == "secret_screen"
    assert result["state"] is None
    assert result["baseline"] is None
    assert "sk-12345678901234567890" not in json.dumps(result)


def test_flow_timeout_keeps_only_safe_bounded_context():
    def handler(request):
        raise httpx.ReadTimeout("private transport detail")

    shadow = with_transport(handler)
    state = {"system": "rules", "user": "candidate"}
    baseline = {"status": "pending"}
    try:
        result = shadow.evaluate("promotion", state, baseline)
    finally:
        close_client(shadow)

    assert result["status"] == "error"
    assert result["error"] == {"category": "timeout", "status": None}
    assert result["state"] == state
    assert result["baseline"] == baseline
    assert "private transport detail" not in json.dumps(result)


def test_empty_extraction_is_skipped_as_empty():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=response_for(request))

    shadow = with_transport(handler)
    try:
        result = shadow.evaluate(
            "extraction",
            {"system": "rules", "conversation": "conversation", "facts": [], "variant": "two_call"},
            {"facts": []},
        )
    finally:
        close_client(shadow)

    assert calls == []
    assert result["status"] == "skipped"
    assert result["reason"] == "empty"
