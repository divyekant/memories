"""Secret redaction: credentials never reach the extraction LLM or storage."""

from transcript_hygiene import redact_secrets


class TestPatterns:
    def test_aws_access_key(self):
        out, types = redact_secrets("creds: AKIA1234567890ABCDEF done")
        assert "AKIA1234567890ABCDEF" not in out
        assert "[REDACTED:aws_access_key]" in out
        assert types == ["aws_access_key"]

    def test_github_token(self):
        out, types = redact_secrets("use ghp_abcdefghijklmnopqrstuvwxyz123456")
        assert "ghp_" not in out.replace("[REDACTED:github_token]", "")
        assert "github_token" in types

    def test_openai_style_key(self):
        out, types = redact_secrets("OPENAI key sk-proj-abc123def456ghi789")
        assert "sk-proj" not in out
        assert "openai_style_key" in types

    def test_jwt(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        out, types = redact_secrets(f"token: {jwt}")
        assert jwt not in out
        assert "jwt" in types

    def test_url_credentials(self):
        out, types = redact_secrets("db at postgres://admin:hunter2secret@db.internal:5432/prod")
        assert "hunter2secret" not in out
        assert "postgres://[REDACTED:url_credentials]@db.internal:5432/prod" in out

    def test_bearer_token(self):
        out, types = redact_secrets("Authorization: Bearer abc123def456ghi789jkl012")
        assert "abc123def456ghi789jkl012" not in out
        assert "bearer_token" in types

    def test_key_value_assignment(self):
        out, types = redact_secrets('config: api_key="god-is-an-astronaut" rest')
        assert "god-is-an-astronaut" not in out
        assert "key_value_secret" in types

    def test_telegram_bot_token(self):
        out, types = redact_secrets("bot 123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw_")
        assert "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw_" not in out
        assert "telegram_bot_token" in types


class TestFalsePositiveGuards:
    def test_prose_about_passwords_untouched(self):
        text = "The password policy doc explains rotation. API keys are stored in the keychain."
        out, types = redact_secrets(text)
        assert out == text
        assert types == []

    def test_short_values_untouched(self):
        out, types = redact_secrets("password: short")
        assert out == "password: short"
        assert types == []

    def test_empty_and_none_safe(self):
        assert redact_secrets("") == ("", [])

    def test_idempotent(self):
        once, _ = redact_secrets("key AKIA1234567890ABCDEF")
        twice, types = redact_secrets(once)
        assert once == twice
        assert types == []


class TestExtractionWiring:
    def test_run_extraction_redacts_before_llm(self, monkeypatch):
        """The extraction prompt must never contain the raw secret."""
        import llm_extract

        captured = {}

        def fake_extract_facts(provider, messages, *a, **k):
            captured["messages"] = messages
            return [], {"input": 0, "output": 0}, None

        monkeypatch.setattr(llm_extract, "extract_facts", fake_extract_facts)
        monkeypatch.setenv("EXTRACT_REDACT_SECRETS", "true")

        from unittest.mock import MagicMock
        engine = MagicMock()
        engine.metadata = []
        llm_extract.run_extraction(
            MagicMock(), engine,
            "User: my token is ghp_abcdefghijklmnopqrstuvwxyz123456 please remember",
            "test/redact", "stop",
        )
        assert "ghp_abcdefghijklmnopqrstuvwxyz123456" not in captured.get("messages", "MISSING")
        assert "[REDACTED:github_token]" in captured.get("messages", "")

    def test_redaction_can_be_disabled(self, monkeypatch):
        import llm_extract

        captured = {}

        def fake_extract_facts(provider, messages, *a, **k):
            captured["messages"] = messages
            return [], {"input": 0, "output": 0}, None

        monkeypatch.setattr(llm_extract, "extract_facts", fake_extract_facts)
        monkeypatch.setenv("EXTRACT_REDACT_SECRETS", "false")

        from unittest.mock import MagicMock
        engine = MagicMock()
        engine.metadata = []
        llm_extract.run_extraction(
            MagicMock(), engine,
            "User: my token is ghp_abcdefghijklmnopqrstuvwxyz123456",
            "test/redact", "stop",
        )
        assert "ghp_abcdefghijklmnopqrstuvwxyz123456" in captured.get("messages", "")


class TestSweepScript:
    def test_scan_logic_pure(self):
        from scripts.redact_corpus import _masked_preview

        preview = _masked_preview("key AKIA1234567890ABCDEF and more text here")
        assert "AKIA1234567890ABCDEF" not in preview


class TestContextGuard:
    """Documentation / placeholder / local-dev / demo content is NOT a secret."""

    def test_placeholder_dsn_password_kept(self):
        out, types = redact_secrets("postgresql://postgres:<SUPABASE_DB_PASSWORD>@db.x.supabase.co:5432/postgres")
        assert types == []

    def test_bracket_placeholder_kept(self):
        out, types = redact_secrets("DSN postgresql://postgres.ref:[PASSWORD]@aws-0.pooler.supabase.com:5432/postgres")
        assert types == []

    def test_localhost_default_kept(self):
        out, types = redact_secrets("postgresql://postgres:postgres@127.0.0.1:54322/postgres")
        assert types == []

    def test_supabase_demo_jwt_kept(self):
        out, types = redact_secrets(
            "Supabase local dev credentials: eyJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJzdXBhYmFzZS1kZW1vIn0.abcdefghij"
        )
        assert types == []

    def test_env_var_name_reference_kept(self):
        out, types = redact_secrets("agent.py reads FPLRECO_LLM_BASE_URL=production at startup")
        assert types == []

    def test_format_example_kept(self):
        out, types = redact_secrets(
            "Telegram bot token format (example: 8732681111:AAELHHQ46ba3pEjqRDp_-sEYvvWmgKR9kwc)"
        )
        assert types == []

    def test_real_secret_still_redacted_near_unrelated_text(self):
        out, types = redact_secrets("prod dsn postgresql://admin:Tr0ub4dor3xKj9w@prod-db.aws.com:5432/app")
        assert "url_credentials" in types

    def test_real_aws_key_not_treated_as_env_name(self):
        out, types = redact_secrets("aws key AKIA1234567890ABCDEF is live")
        assert "aws_access_key" in types
