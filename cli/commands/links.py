"""Links CLI commands — list, add, remove memory links."""

import click

from cli import app, pass_ctx
from cli.commands.core import handle_errors


@app.group()
def links():
    """Manage links between memories."""


@links.command("list")
@click.argument("memory_id", type=int)
@pass_ctx
@handle_errors
def links_list(ctx, memory_id):
    """List all links for a memory."""
    data = ctx.client.get_links(memory_id, include_incoming=True)

    def human(d):
        items = d.get("links", [])
        if not items:
            click.echo("No links found.")
            return
        click.secho(f"{len(items)} link(s):\n", fg="yellow")
        for link in items:
            direction = link.get("direction", "outgoing")
            target = link["to_id"] if direction == "outgoing" else link["from_id"]
            lt = link.get("link_type", link.get("type", "unknown"))
            arrow = "→" if direction == "outgoing" else "←"
            click.echo(f"  {memory_id} {arrow} {target}  ({lt})")

    ctx.fmt.echo(data, human)


@links.command("add")
@click.argument("from_id", type=int)
@click.argument("to_id", type=int)
@click.option("--type", "link_type", default="related_to",
              type=click.Choice(["related_to", "reinforces", "supersedes", "blocked_by", "caused_by"]))
@pass_ctx
@handle_errors
def links_add(ctx, from_id, to_id, link_type):
    """Create a link between two memories."""
    data = ctx.client.add_link(from_id, to_id, link_type)

    def human(d):
        click.secho(f"Link created: {from_id} →({link_type})→ {to_id}", fg="green")

    ctx.fmt.echo(data, human)


@links.command("remove")
@click.argument("from_id", type=int)
@click.argument("to_id", type=int)
@click.option("--type", "link_type", default="related_to",
              type=click.Choice(["related_to", "reinforces", "supersedes", "blocked_by", "caused_by"]))
@pass_ctx
@handle_errors
def links_remove(ctx, from_id, to_id, link_type):
    """Remove a link between two memories."""
    data = ctx.client.remove_link(from_id, to_id, link_type)

    def human(d):
        click.secho(f"Link removed: {from_id} ✕ {to_id} ({link_type})", fg="yellow")

    ctx.fmt.echo(data, human)
