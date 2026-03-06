"""Config CLI commands — show and set configuration values."""

import click

from cli import app, pass_ctx
from cli.commands.core import handle_errors
from cli.config import resolve_config, write_config

VALID_KEYS = {"url", "api_key", "default_source"}


@app.group("config")
def config_group():
    """Manage CLI configuration."""
    pass


@config_group.command("show")
@pass_ctx
@handle_errors
def config_show(ctx):
    """Display resolved configuration with source attribution."""
    cfg = resolve_config()  # get fresh resolution

    def human(d):
        for key in ["url", "api_key"]:
            value = d.get(key, "not set")
            source = d.get("_sources", {}).get(key, "?")
            if key == "api_key" and value and value != "not set":
                value = value[:8] + "****"
            click.echo(f"{key:>15}: {value}  (from {source})")

    ctx.fmt.echo(cfg, human_fn=human)


@config_group.command("set")
@click.argument("key")
@click.argument("value")
@pass_ctx
@handle_errors
def config_set(ctx, key, value):
    """Set a configuration value."""
    if key not in VALID_KEYS:
        raise click.UsageError(
            f"Invalid key '{key}'. Valid keys: {', '.join(sorted(VALID_KEYS))}"
        )
    path = write_config(**{key: value})
    data = {"key": key, "value": value, "file": str(path)}
    ctx.fmt.echo(
        data,
        human_fn=lambda d: click.echo(f"Set {d['key']} in {d['file']}"),
    )
