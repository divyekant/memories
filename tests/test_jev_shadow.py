import json

import httpx
import pytest

from jev_shadow import JevShadow


ACTIONS = {"ADD", "UPDATE", "DELETE", "NOOP", "CONFLICT"}


@pytest.fixture(autouse=True)
def fake_typesafe_key(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")


def prompt_for(*, fact_text="new fact", memory_id=7):
    facts = [{"index": 0, "text": fact_text, "category": "detail"}]
    similar = {"0": [{"id": memory_id, "text": "old fact", "relevance": 0.9}]}
    return (
        "New facts:\n"
        f"{json.dumps(facts)}\n\n"
        "Existing similar memories (per fact):\n"
        f"{json.dumps(similar)}\n\n"
        "Output a JSON array."
    )


def answer(choice, criteria, confidence=0.9):
    probabilities = {key: 0.0 for key in criteria}
    probabilities[choice] = 1.0
    return {
        "type": "choice",
        "choice": choice,
        "probabilities": probabilities,
        "confidence": confidence,
    }


def response_for(*, action="NOOP", target="m_7", confidence=0.9):
    return {
        "model": "jev-latest-served",
        "answers": {
            "action_0": answer(action, {name: None for name in ACTIONS}, confidence),
            "target_0": answer(target, {"none": None, "m_7": None}, confidence),
        },
        "usage": {"input_tokens": 12, "output_tokens": 8},
    }


def with_transport(handler):
    shadow = JevShadow()
    shadow._client = httpx.Client(transport=httpx.MockTransport(handler))
    return shadow


def close_client(shadow):
    shadow._client.close()


def test_direct_payload_excludes_primary_oracle_and_keeps_full_prompt():
    seen = []
    system = "memory manager rules"
    user = prompt_for()
    primary = (
        '[{"action":"NOOP","fact_index":0,"existing_id":7,'
        '"new_text":"ORACLE_MUST_NOT_BE_SENT"}]'
    )

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=response_for())

    shadow = with_transport(handler)
    try:
        result = shadow.compare(system, user, primary)
    finally:
        close_client(shadow)

    assert result["status"] == "ok"
    assert result["primary_decisions"] == [
        {"fact_index": 0, "action": "NOOP", "target": 7, "target_valid": True}
    ]
    assert result["shadow_decisions"] == [
        {"fact_index": 0, "action": "NOOP", "target": 7, "target_valid": True}
    ]
    assert result["action_matches"] == 1
    assert result["joint_matches"] == 1
    assert result["invalid_targets"] == 0
    assert result["prompt"] == {"system": system, "user": user}
    assert result["served_model"] == "jev-latest-served"
    assert result["shadow_input_tokens"] == 12
    assert result["shadow_output_tokens"] == 8

    request = seen[0]
    payload = json.loads(request.content)
    assert str(request.url) == "https://api.typesafe.ai/v1/systemone"
    assert request.headers["authorization"] == "Bearer test-key"
    assert request.headers["content-type"] == "application/json"
    assert set(payload) == {"model", "state", "questions"}
    assert payload["model"] == "jev-latest"
    assert payload["state"] == {"system": system, "user": user}
    assert "ORACLE_MUST_NOT_BE_SENT" not in json.dumps(payload)
    assert set(payload["questions"]["action_0"]["criteria"]) == ACTIONS
    assert set(payload["questions"]["target_0"]["criteria"]) == {"none", "m_7"}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda body: body["answers"].pop("action_0"),
        lambda body: body["answers"]["action_0"].update({"type": "score"}),
        lambda body: body["answers"]["action_0"].update({"choice": "DEFER"}),
        lambda body: body["answers"]["action_0"]["probabilities"].update({"BAD": 0.0}),
        lambda body: body["answers"]["action_0"].update({"confidence": 1.1}),
        lambda body: body["answers"]["action_0"]["probabilities"].update({"ADD": 2.0}),
        lambda body: body["usage"].update({"input_tokens": "12"}),
    ],
)
def test_malformed_outcomes_are_safe_errors(mutate):
    body = response_for()
    mutate(body)

    def handler(request):
        return httpx.Response(200, json=body)

    shadow = with_transport(handler)
    try:
        result = shadow.compare("rules", prompt_for(), "[]")
    finally:
        close_client(shadow)

    assert result["status"] == "error"
    assert result["error"]["category"] == "invalid_response"
    assert result["error"]["status"] == 200
    assert set(result["error"]) == {"category", "status"}
    assert "answers" not in result["error"]
    assert result["prompt"]["user"] == prompt_for()


def test_add_with_target_stays_invalid_and_joint_match_excludes_it():
    primary = '[{"action":"ADD","fact_index":0,"old_id":7}]'

    def handler(request):
        return httpx.Response(200, json=response_for(action="ADD", target="m_7"))

    shadow = with_transport(handler)
    try:
        result = shadow.compare("rules", prompt_for(), primary)
    finally:
        close_client(shadow)

    assert result["primary_decisions"][0] == {
        "fact_index": 0,
        "action": "ADD",
        "target": 7,
        "target_valid": False,
    }
    assert result["shadow_decisions"][0] == {
        "fact_index": 0,
        "action": "ADD",
        "target": 7,
        "target_valid": False,
    }
    assert result["action_matches"] == 1
    assert result["joint_matches"] == 0
    assert result["invalid_targets"] == 1


def test_invalid_primary_format_does_not_claim_agreement():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=response_for())

    shadow = with_transport(handler)
    try:
        result = shadow.compare("rules", prompt_for(), "```json\nnot json\n```")
    finally:
        close_client(shadow)

    assert seen
    assert result["status"] == "ok"
    assert result["primary_decisions"] == []
    assert result["primary_parse_error"] == "invalid_primary_decisions"
    assert result["action_matches"] is None
    assert result["joint_matches"] is None
    assert result["shadow_decisions"]


def test_http_error_is_structured_and_does_not_retry():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            401,
            json={"detail": "Authorization Bearer sk-never-log-this"},
            headers={"x-secret": "do-not-log"},
        )

    shadow = with_transport(handler)
    try:
        result = shadow.compare("rules", prompt_for(), "[]")
    finally:
        close_client(shadow)

    assert len(calls) == 1
    assert result["status"] == "error"
    assert result["error"] == {"category": "http", "status": 401}
    assert "sk-never-log-this" not in json.dumps(result)
    assert "do-not-log" not in json.dumps(result)


def test_timeout_is_safe_and_does_not_retry():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("secret transport detail")

    shadow = with_transport(handler)
    try:
        result = shadow.compare("rules", prompt_for(), "[]")
    finally:
        close_client(shadow)

    assert len(calls) == 1
    assert result["status"] == "error"
    assert result["error"] == {"category": "timeout", "status": None}
    assert "secret transport detail" not in json.dumps(result)


@pytest.mark.parametrize(
    "secret",
    [
        "vck_abcdefghijklmnopqrstuvwxyz",
        "apikey_0123456789abcdef_abcdef0123456789",
        "sk-12345678901234567890",
    ],
)
def test_secret_screen_runs_before_network(secret):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=response_for())

    shadow = with_transport(handler)
    try:
        result = shadow.compare(f"rules {secret}", prompt_for(), "[]")
    finally:
        close_client(shadow)

    assert calls == []
    assert result["status"] == "skipped"
    assert result["reason"] == "secret_screen"
    assert result["prompt"] is None


def test_missing_key_fails_with_safe_message(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(ValueError, match=r"^TYPESAFE_API_KEY is required$"):
        JevShadow()
