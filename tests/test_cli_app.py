"""Tests for Click app skeleton."""

from click.testing import CliRunner

from cli import app


class TestHelp:
    def test_help_shows_usage(self):
        runner = CliRunner()
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "Memories" in result.output
        assert "Usage" in result.output

    def test_help_shows_options(self):
        runner = CliRunner()
        result = runner.invoke(app, ["--help"])
        assert "--url" in result.output
        assert "--api-key" in result.output
        assert "--json" in result.output
        assert "--pretty" in result.output


class TestVersion:
    def test_version_shows_version(self):
        runner = CliRunner()
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "version" in result.output.lower()


class TestGlobalOptions:
    def test_url_option_accepted(self):
        runner = CliRunner()
        result = runner.invoke(app, ["--url", "http://other:9999", "--help"])
        assert result.exit_code == 0

    def test_json_flag_accepted(self):
        runner = CliRunner()
        result = runner.invoke(app, ["--json", "--help"])
        assert result.exit_code == 0

    def test_pretty_flag_accepted(self):
        runner = CliRunner()
        result = runner.invoke(app, ["--pretty", "--help"])
        assert result.exit_code == 0
