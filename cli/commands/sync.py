"""Sync CLI commands -- status, upload, download, snapshots, restore."""

import click

from cli import app, pass_ctx
from cli.commands.core import handle_errors


@app.group()
def sync():
    """Remote sync operations."""


@sync.command("status")
@pass_ctx
@handle_errors
def sync_status(ctx):
    """Show sync status."""
    data = ctx.client.sync_status()

    def human(d):
        enabled = d.get("enabled", False)
        click.echo(f"Sync enabled: {enabled}")
        remote = d.get("latest_remote", d.get("remote_snapshot", ""))
        local = d.get("latest_local", d.get("local_snapshot", ""))
        if remote:
            click.echo(f"Latest remote: {remote}")
        if local:
            click.echo(f"Latest local: {local}")
        remote_count = d.get("remote_count", d.get("remote_snapshots"))
        local_count = d.get("local_count", d.get("local_snapshots"))
        if remote_count is not None:
            click.echo(f"Remote snapshots: {remote_count}")
        if local_count is not None:
            click.echo(f"Local snapshots: {local_count}")

    ctx.fmt.echo(data, human)


@sync.command("upload")
@pass_ctx
@handle_errors
def sync_upload(ctx):
    """Upload local data to remote."""
    data = ctx.client.sync_upload()

    def human(d):
        message = d.get("message", d.get("status", "Upload complete"))
        click.secho(message, fg="green")

    ctx.fmt.echo(data, human)


@sync.command("download")
@click.option("--backup-name", default=None, help="Specific backup to download")
@click.option("--confirm", is_flag=True, help="Confirm destructive download")
@pass_ctx
@handle_errors
def sync_download(ctx, backup_name, confirm):
    """Download remote data to local."""
    data = ctx.client.sync_download(backup_name, confirm)

    def human(d):
        message = d.get("message", d.get("status", "Download complete"))
        click.secho(message, fg="green")

    ctx.fmt.echo(data, human)


@sync.command("snapshots")
@pass_ctx
@handle_errors
def sync_snapshots(ctx):
    """List remote snapshots."""
    data = ctx.client.sync_snapshots()

    def human(d):
        snapshots = d.get("snapshots", [])
        if not snapshots:
            click.echo("No snapshots found.")
            return
        for s in snapshots:
            if isinstance(s, str):
                click.echo(f"  {s}")
            else:
                name = s.get("name", s.get("key", "?"))
                click.echo(f"  {name}")

    ctx.fmt.echo(data, human)


@sync.command("restore")
@click.argument("name")
@click.option("--confirm", is_flag=True, help="Confirm destructive restore")
@pass_ctx
@handle_errors
def sync_restore(ctx, name, confirm):
    """Restore from a remote snapshot."""
    data = ctx.client.sync_restore(name, confirm)

    def human(d):
        message = d.get("message", d.get("status", "Restore complete"))
        click.secho(message, fg="green")

    ctx.fmt.echo(data, human)
