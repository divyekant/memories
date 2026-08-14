from unittest.mock import MagicMock

from project_memory import TrustedAuthorship
from project_promotion import PromotionContext, PromotionMode, PromotionStatus


class TestSingleCallExtraction:

    def test_single_call_returns_actions_list(self):
        from llm_extract import extract_and_decide_single_call
        mock_provider = MagicMock()
        mock_result = MagicMock()
        mock_result.text = '[{"action": "ADD", "fact_index": 0, "category": "decision", "text": "Use PostgreSQL"}]'
        mock_result.input_tokens = 100
        mock_result.output_tokens = 50
        mock_provider.complete.return_value = mock_result
        mock_engine = MagicMock()

        actions, usage, _ = extract_and_decide_single_call(
            provider=mock_provider, messages="We decided to use PostgreSQL.",
            source="test/", engine=mock_engine,
        )
        assert isinstance(actions, list)
        assert len(actions) == 1
        assert actions[0]["action"] == "ADD"

    def test_single_call_uses_one_llm_call(self):
        from llm_extract import extract_and_decide_single_call
        mock_provider = MagicMock()
        mock_result = MagicMock()
        mock_result.text = '[{"action": "NOOP", "fact_index": 0}]'
        mock_result.input_tokens = 100
        mock_result.output_tokens = 50
        mock_provider.complete.return_value = mock_result
        mock_engine = MagicMock()

        extract_and_decide_single_call(
            provider=mock_provider, messages="Some text",
            source="test/", engine=mock_engine,
        )
        assert mock_provider.complete.call_count == 1

    def test_run_extraction_dispatches_single_call_from_profile(self):
        from llm_extract import run_extraction
        mock_provider = MagicMock()
        mock_result = MagicMock()
        mock_result.text = '[{"action": "ADD", "fact_index": 0, "category": "decision", "text": "Use Redis"}]'
        mock_result.input_tokens = 100
        mock_result.output_tokens = 50
        mock_provider.complete.return_value = mock_result
        mock_engine = MagicMock()
        mock_engine.add_memories.return_value = [1]

        profile = {"single_call": True, "mode": "standard",
                   "max_facts": 30, "max_fact_chars": 500, "rules": {}}

        result = run_extraction(
            provider=mock_provider, engine=mock_engine,
            messages="We chose Redis for caching.", source="test/",
            profile=profile,
        )
        assert mock_provider.complete.call_count == 1

    def test_single_call_handles_invalid_json(self):
        from llm_extract import extract_and_decide_single_call
        mock_provider = MagicMock()
        mock_result = MagicMock()
        mock_result.text = 'not valid json'
        mock_result.input_tokens = 100
        mock_result.output_tokens = 50
        mock_provider.complete.return_value = mock_result
        mock_engine = MagicMock()

        actions, _, _ = extract_and_decide_single_call(
            provider=mock_provider, messages="text",
            source="test/", engine=mock_engine,
        )
        assert actions == []

    def test_single_call_captures_active_promotion_proposal(self, monkeypatch):
        from llm_extract import run_extraction

        monkeypatch.setenv("PROJECT_PROMOTION_RELEVANCE_THRESHOLD", "0.8")
        mock_provider = MagicMock()
        mock_result = MagicMock()
        mock_result.text = (
            '[{"action":"ADD","fact_index":0,"category":"decision",'
            '"text":"Use PostgreSQL","project_relevance":0.95,'
            '"visibility":"project","assertion_status":"confirmed",'
            '"project_kind":"knowledge","confidence":0.94,'
            '"reason":"Confirmed project decision"}]'
        )
        mock_result.input_tokens = 100
        mock_result.output_tokens = 50
        mock_provider.complete.return_value = mock_result
        mock_engine = MagicMock()
        mock_engine.add_memories.return_value = [17]
        context = PromotionContext(
            project_id="demo",
            principal_id="alice",
            declared_mode=PromotionMode.AUTO,
            effective_mode=PromotionMode.AUTO,
            declaration_fingerprint="a" * 64,
            classifier_version="classifier-v1",
            classifier_provider="anthropic",
            classifier_model="claude-haiku",
            reviewer_version="reviewer-v1",
            reviewer_provider="anthropic",
            reviewer_model="claude-haiku",
        )

        result = run_extraction(
            provider=mock_provider,
            engine=mock_engine,
            messages="We decided to use PostgreSQL.",
            source="person/alice/demo/knowledge",
            allowed_prefixes=["person/alice/demo", "project/demo"],
            trusted_authorship=TrustedAuthorship.principal("alice", "codex"),
            promotion_context=context,
            profile={"single_call": True, "mode": "standard", "rules": {}},
        )

        assert result["promotion_candidates"] == [
            {"candidate_id": 17, "fact_index": 0, "route": "ordinary"}
        ]
        assert "project_relevance" in mock_provider.complete.call_args.kwargs["system"]
        assert (
            mock_engine.add_memories.call_args.kwargs["trusted_promotion"].status
            is PromotionStatus.CANDIDATE
        )

    def test_promotion_callback_failure_marks_candidate_without_rollback(self, monkeypatch):
        from llm_extract import run_extraction

        monkeypatch.setenv("PROJECT_PROMOTION_RELEVANCE_THRESHOLD", "0.8")
        mock_provider = MagicMock()
        mock_result = MagicMock()
        mock_result.text = (
            '[{"action":"ADD","fact_index":0,"category":"decision",'
            '"text":"Use PostgreSQL","project_relevance":0.95,'
            '"visibility":"project","assertion_status":"confirmed",'
            '"project_kind":"knowledge","confidence":0.94,'
            '"reason":"Confirmed project decision"}]'
        )
        mock_result.input_tokens = 100
        mock_result.output_tokens = 50
        mock_provider.complete.return_value = mock_result
        mock_engine = MagicMock()
        mock_engine.add_memories.return_value = [17]
        context = PromotionContext(
            project_id="demo",
            principal_id="alice",
            declared_mode=PromotionMode.AUTO,
            effective_mode=PromotionMode.AUTO,
            declaration_fingerprint="a" * 64,
            classifier_version="classifier-v1",
            classifier_provider="anthropic",
            classifier_model="claude-haiku",
            reviewer_version="reviewer-v1",
            reviewer_provider="anthropic",
            reviewer_model="claude-haiku",
        )

        def stored_candidate(_candidate_id):
            state = mock_engine.add_memories.call_args.kwargs["trusted_promotion"]
            return {"id": 17, "source": "person/alice/demo/knowledge", **state.as_metadata()}

        mock_engine.get_memory.side_effect = stored_candidate

        def fail_callback(_candidates, _evidence):
            raise RuntimeError("review queue unavailable")

        result = run_extraction(
            provider=mock_provider,
            engine=mock_engine,
            messages="We decided to use PostgreSQL.",
            source="person/alice/demo/knowledge",
            allowed_prefixes=["person/alice/demo", "project/demo"],
            trusted_authorship=TrustedAuthorship.principal("alice", "codex"),
            promotion_context=context,
            promotion_callback=fail_callback,
            profile={"single_call": True, "mode": "standard", "rules": {}},
        )

        assert result["promotion_callback_error"] == "review queue unavailable"
        assert result["promotion_candidates"] == [
            {"candidate_id": 17, "fact_index": 0, "route": "ordinary"}
        ]
        assert mock_engine.add_memories.call_count == 1
        failed_state = mock_engine.update_memory.call_args.kwargs["trusted_promotion"]
        assert failed_state.status is PromotionStatus.FAILED
