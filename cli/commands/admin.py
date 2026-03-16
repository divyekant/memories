"""Admin CLI commands -- stats, health, metrics, usage, deduplicate, consolidate, prune, reload-embedder, conflicts."""

import click

from cli import app, pass_ctx
from cli.commands.core import handle_errors


@app.group()
def admin():
    """Server administration commands."""


@admin.command("stats")
@pass_ctx
@handle_errors
def admin_stats(ctx):
    """Show server statistics."""
    data = ctx.client.stats()

    def human(d):
        for key, value in d.items():
            click.echo(f"{key}: {value}")

    ctx.fmt.echo(data, human)


@admin.command("health")
@pass_ctx
@handle_errors
def admin_health(ctx):
    """Show server health status."""
    data = ctx.client.health()

    def human(d):
        status = d.get("status", "unknown")
        version = d.get("version", "?")
        total = d.get("total_memories", d.get("memories", "?"))
        click.echo(f"Status: {status}")
        click.echo(f"Version: {version}")
        click.echo(f"Memories: {total}")

    ctx.fmt.echo(data, human)


@admin.command("metrics")
@pass_ctx
@handle_errors
def admin_metrics(ctx):
    """Show server metrics."""
    data = ctx.client.metrics()

    def human(d):
        for key, value in d.items():
            click.echo(f"{key}: {value}")

    ctx.fmt.echo(data, human)


@admin.command("usage")
@click.option("--period", default="7d",
              type=click.Choice(["today", "7d", "30d", "all"]),
              help="Usage period")
@pass_ctx
@handle_errors
def admin_usage(ctx, period):
    """Show usage statistics."""
    data = ctx.client.usage(period)

    def human(d):
        for key, value in d.items():
            click.echo(f"{key}: {value}")

    ctx.fmt.echo(data, human)


@admin.command("deduplicate")
@click.option("--threshold", default=0.90, type=float,
              help="Similarity threshold for deduplication")
@click.option("--dry-run/--execute", default=True,
              help="Preview duplicates or execute removal")
@pass_ctx
@handle_errors
def admin_deduplicate(ctx, threshold, dry_run):
    """Find and remove duplicate memories."""
    data = ctx.client.deduplicate(threshold, dry_run)

    def human(d):
        count = d.get("duplicates", d.get("count", 0))
        if dry_run:
            click.echo(f"Found {count} duplicates (dry run)")
        else:
            click.secho(f"Removed {count} duplicates", fg="yellow")

    ctx.fmt.echo(data, human)


@admin.command("consolidate")
@pass_ctx
@handle_errors
def admin_consolidate(ctx):
    """Consolidate memory storage."""
    data = ctx.client.consolidate()

    def human(d):
        message = d.get("message", d.get("status", "Consolidation complete"))
        click.echo(message)

    ctx.fmt.echo(data, human)


@admin.command("prune")
@pass_ctx
@handle_errors
def admin_prune(ctx):
    """Prune stale or orphaned data."""
    data = ctx.client.prune()

    def human(d):
        pruned = d.get("pruned", d.get("count", 0))
        click.secho(f"Pruned {pruned} entries", fg="yellow")

    ctx.fmt.echo(data, human)


@admin.command("conflicts")
@pass_ctx
@handle_errors
def admin_conflicts(ctx):
    """List memories that conflict with each other."""
    data = ctx.client.conflicts()

    def human(d):
        conflicts = d.get("conflicts", [])
        if not conflicts:
            click.echo("No conflicts found.")
            return
        click.secho(f"{len(conflicts)} conflict(s):\n", fg="yellow")
        for c in conflicts:
            other = c.get("conflicting_memory")
            other_text = other["text"][:100] if other else "(deleted)"
            click.echo(f"  [{c['id']}] {c['text'][:100]}")
            click.secho(f"    conflicts with [{c['conflicts_with']}] {other_text}", fg="red")
            click.echo()

    ctx.fmt.echo(data, human)


@admin.command("reload-embedder")
@pass_ctx
@handle_errors
def admin_reload_embedder(ctx):
    """Reload the embedding model."""
    data = ctx.client.reload_embedder()

    def human(d):
        status = d.get("status", d.get("message", "Embedder reloaded"))
        click.echo(f"Reload: {status}")

    ctx.fmt.echo(data, human)
