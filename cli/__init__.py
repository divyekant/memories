"""Memories CLI — full-coverage command-line interface."""

import click

from cli.config import resolve_config
from cli.client import MemoriesClient
from cli.output import OutputFormatter


class Context:
    """Shared context passed to all commands."""

    def __init__(self, client: MemoriesClient, formatter: OutputFormatter):
        self.client = client
        self.fmt = formatter


pass_ctx = click.make_pass_decorator(Context)


@click.group()
@click.option("--url", default=None, help="Memories server URL")
@click.option("--api-key", default=None, help="API key for authentication")
@click.option("--json", "force_json", is_flag=True, help="Force JSON output")
@click.option("--pretty", "force_pretty", is_flag=True,
              help="Force human-readable output")
@click.version_option(package_name="memories")
@click.pass_context
def app(ctx, url, api_key, force_json, force_pretty):
    """Memories — local semantic memory for AI assistants."""
    cfg = resolve_config(flag_url=url, flag_api_key=api_key)
    client = MemoriesClient(url=cfg["url"], api_key=cfg["api_key"])
    formatter = OutputFormatter(force_json=force_json, force_pretty=force_pretty)
    ctx.ensure_object(dict)
    ctx.obj = Context(client=client, formatter=formatter)


from cli.commands import core  # noqa: E402, F401
from cli.commands import batch  # noqa: E402, F401
from cli.commands import delete_by  # noqa: E402, F401
from cli.commands import admin  # noqa: E402, F401
from cli.commands import backup  # noqa: E402, F401
from cli.commands import sync  # noqa: E402, F401
from cli.commands import extract  # noqa: E402, F401
from cli.commands import auth  # noqa: E402, F401
from cli.commands import config_cmd  # noqa: E402, F401
from cli.commands import export_import  # noqa: E402, F401
from cli.commands import links  # noqa: E402, F401
from cli.commands import eval_cmd  # noqa: E402, F401
