"""Output formatting with TTY auto-detection and JSON envelope."""

import json
import sys

import click


def format_json_envelope(data: dict) -> str:
    """Return a JSON success envelope."""
    return json.dumps({"ok": True, "data": data}, default=str)


def format_error_envelope(message: str, code: str = "GENERAL_ERROR") -> str:
    """Return a JSON error envelope."""
    return json.dumps({"ok": False, "error": message, "code": code})


class OutputFormatter:
    """Format output for CLI commands with TTY auto-detection."""

    def __init__(self, force_json=False, force_pretty=False, stream=None):
        self._force_json = force_json
        self._force_pretty = force_pretty
        self._stream = stream or sys.stdout

    @property
    def is_json(self) -> bool:
        if self._force_json:
            return True
        if self._force_pretty:
            return False
        return not self._stream.isatty()

    def success(self, data: dict) -> str:
        return format_json_envelope(data)

    def error(self, message: str, code: str = "GENERAL_ERROR") -> str:
        return format_error_envelope(message, code)

    def echo(self, data: dict, human_fn=None):
        if self.is_json:
            click.echo(self.success(data))
        elif human_fn:
            human_fn(data)
        else:
            click.echo(self.success(data))

    def echo_error(self, message: str, code: str = "GENERAL_ERROR"):
        if self.is_json:
            click.echo(self.error(message, code), err=True)
        else:
            click.secho(f"Error: {message}", fg="red", err=True)
