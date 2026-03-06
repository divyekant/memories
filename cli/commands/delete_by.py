"""Delete-by CLI commands — delete memories by source pattern or prefix."""

import click

from cli import app, pass_ctx
from cli.commands.core import handle_errors


@app.group("delete-by")
def delete_by():
    """Delete memories by source pattern or prefix."""


@delete_by.command("source")
@click.argument("pattern")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
@pass_ctx
@handle_errors
def delete_by_source(ctx, pattern, yes):
    """Delete all memories matching a source pattern."""
    if not yes:
        confirmed = click.confirm(
            f"Delete all memories matching source pattern '{pattern}'?",
            default=False,
        )
        if not confirmed:
            click.echo("Aborted.")
            return

    data = ctx.client.delete_by_source(pattern)

    def human(d):
        deleted = d.get("deleted", 0)
        click.secho(f"Deleted {deleted} memories matching '{pattern}'", fg="yellow")

    ctx.fmt.echo(data, human)


@delete_by.command("prefix")
@click.argument("prefix")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
@pass_ctx
@handle_errors
def delete_by_prefix(ctx, prefix, yes):
    """Delete all memories with a given prefix."""
    if not yes:
        confirmed = click.confirm(
            f"Delete all memories matching source pattern '{prefix}'?",
            default=False,
        )
        if not confirmed:
            click.echo("Aborted.")
            return

    data = ctx.client.delete_by_prefix(prefix)

    def human(d):
        deleted = d.get("deleted", 0)
        click.secho(f"Deleted {deleted} memories with prefix '{prefix}'", fg="yellow")

    ctx.fmt.echo(data, human)
