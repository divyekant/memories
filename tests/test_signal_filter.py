"""Tests for the signal keyword pre-filter grep pattern used in extraction hooks."""

import subprocess

DEFAULT_KEYWORDS = "decide|decision|chose|bug|fix|remember|architecture|convention|pattern|learning|mistake"


def _grep_matches(text, keywords=DEFAULT_KEYWORDS):
    result = subprocess.run(
        ["grep", "-qiE", keywords],
        input=text,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def test_matches_decision():
    assert _grep_matches("We decided to use PostgreSQL")


def test_matches_bug():
    assert _grep_matches("Found a bug in auth")


def test_matches_fix():
    assert _grep_matches("Applied a fix for the login issue")


def test_matches_remember():
    assert _grep_matches("Remember to update the config")


def test_matches_architecture():
    assert _grep_matches("The architecture uses microservices")


def test_matches_convention():
    assert _grep_matches("Our convention is to use snake_case")


def test_matches_pattern():
    assert _grep_matches("This follows the observer pattern")


def test_matches_learning():
    assert _grep_matches("Key learning from the outage")


def test_matches_mistake():
    assert _grep_matches("That was a mistake in the deployment")


def test_matches_chose():
    assert _grep_matches("We chose Redis for caching")


def test_no_match_skips():
    assert not _grep_matches("Just running tests")


def test_no_match_generic_conversation():
    assert not _grep_matches("Hello, how are you doing today?")


def test_no_match_code_output():
    assert not _grep_matches("Total: 42 items processed successfully")


def test_case_insensitive():
    assert _grep_matches("ARCHITECTURE decision")


def test_case_insensitive_mixed():
    assert _grep_matches("Found a BUG in the system")


def test_custom_keywords():
    assert _grep_matches("important", "important|critical")


def test_custom_keywords_no_match():
    assert not _grep_matches("just testing", "important|critical")


def test_multiline_input():
    text = "Line one has nothing special\nLine two mentions a bug\nLine three is fine"
    assert _grep_matches(text)


def test_multiline_no_match():
    text = "Line one\nLine two\nLine three"
    assert not _grep_matches(text)


def test_keyword_as_substring():
    """Keywords match as substrings — 'decide' matches 'undecided'."""
    assert _grep_matches("She was undecided about the approach")


def test_empty_input():
    assert not _grep_matches("")
