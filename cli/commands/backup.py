"""Backup CLI commands -- create, list, restore."""

import click

from cli import app, pass_ctx
from cli.commands.core import handle_errors


@app.group()
def backup():
    """Backup and restore operations."""


@backup.command("create")
@click.option("--prefix", default="manual", help="Backup name prefix")
@pass_ctx
@handle_errors
def backup_create(ctx, prefix):
    """Create a new backup."""
    data = ctx.client.backup_create(prefix)

    def human(d):
        path = d.get("path", d.get("backup", d.get("name", "?")))
        click.secho(f"Backup created: {path}", fg="green")

    ctx.fmt.echo(data, human)


@backup.command("list")
@pass_ctx
@handle_errors
def backup_list(ctx):
    """List available backups."""
    data = ctx.client.backup_list()

    def human(d):
        backups = d.get("backups", [])
        if not backups:
            click.echo("No backups found.")
            return
        for b in backups:
            name = b.get("name", b.get("path", "?"))
            date = b.get("created_at", b.get("date", ""))
            if date:
                click.echo(f"  {name}  ({date})")
            else:
                click.echo(f"  {name}")

    ctx.fmt.echo(data, human)


@backup.command("restore")
@click.argument("name")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
@pass_ctx
@handle_errors
def backup_restore(ctx, name, yes):
    """Restore from a backup."""
    if not yes:
        confirmed = click.confirm(
            f"Restore from backup '{name}'? This will replace current data.",
            default=False,
        )
        if not confirmed:
            click.echo("Aborted.")
            return

    data = ctx.client.backup_restore(name)

    def human(d):
        restored = d.get("restored", d.get("count", "?"))
        click.secho(f"Restored {restored} memories from '{name}'", fg="green")

    ctx.fmt.echo(data, human)
