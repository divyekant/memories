"""Batch CLI commands — add, get, delete, search, upsert in bulk."""

import json
import sys
from pathlib import Path

import click

from cli import app, pass_ctx
from cli.commands.core import handle_errors


def read_input(file_arg):
    """Read from file path or stdin (when arg is '-' or stdin has data)."""
    if file_arg == "-" or (file_arg is None and not sys.stdin.isatty()):
        return sys.stdin.read()
    path = Path(file_arg)
    if not path.exists():
        raise click.UsageError(f"File not found: {file_arg}")
    return path.read_text()


def parse_jsonl(text):
    """Parse JSON or JSONL input into a list of dicts."""
    text = text.strip()
    if text.startswith("["):
        return json.loads(text)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


@app.group()
def batch():
    """Batch operations on memories."""


@batch.command("add")
@click.argument("file", default="-")
@pass_ctx
@handle_errors
def batch_add(ctx, file):
    """Add memories in batch from JSON/JSONL file."""
    raw = read_input(file)
    memories = parse_jsonl(raw)
    data = ctx.client.add_batch(memories)

    def human(d):
        added = d.get("added", d.get("count", len(memories)))
        click.secho(f"Added {added} memories", fg="green")

    ctx.fmt.echo(data, human)


@batch.command("get")
@click.argument("ids", nargs=-1, required=True, type=int)
@pass_ctx
@handle_errors
def batch_get(ctx, ids):
    """Get multiple memories by ID."""
    data = ctx.client.get_batch([str(i) for i in ids])

    def human(d):
        memories = d.get("memories", [])
        for m in memories:
            mid = m.get("id", "?")
            src = m.get("source", "")
            text = m.get("text", "")
            if len(text) > 80:
                text = text[:80] + "..."
            click.echo(f"  [{mid}] {src}  {text}")

    ctx.fmt.echo(data, human)


@batch.command("delete")
@click.argument("ids", nargs=-1, required=True, type=int)
@pass_ctx
@handle_errors
def batch_delete(ctx, ids):
    """Delete multiple memories by ID."""
    data = ctx.client.delete_batch([str(i) for i in ids])

    def human(d):
        deleted = d.get("deleted", len(ids))
        click.secho(f"Deleted {deleted} memories", fg="yellow")

    ctx.fmt.echo(data, human)


@batch.command("search")
@click.argument("file", default="-")
@pass_ctx
@handle_errors
def batch_search(ctx, file):
    """Run batch searches from JSON/JSONL file."""
    raw = read_input(file)
    queries = parse_jsonl(raw)
    data = ctx.client.search_batch(queries)

    def human(d):
        results = d.get("results", [])
        click.echo(f"Ran {len(results)} queries")
        for i, r in enumerate(results):
            hits = r.get("results", [])
            click.echo(f"  Query {i + 1}: {len(hits)} results")

    ctx.fmt.echo(data, human)


@batch.command("upsert")
@click.argument("file", default="-")
@pass_ctx
@handle_errors
def batch_upsert(ctx, file):
    """Upsert memories in batch from JSON/JSONL file."""
    raw = read_input(file)
    memories = parse_jsonl(raw)
    data = ctx.client.upsert_batch(memories)

    def human(d):
        count = d.get("count", d.get("upserted", len(memories)))
        click.secho(f"Upserted {count} memories", fg="green")

    ctx.fmt.echo(data, human)
