from unittest.mock import MagicMock


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
