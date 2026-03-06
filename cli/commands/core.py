"""Core CLI commands — search, add, get, list, delete, count, upsert, is-novel, folders."""

import functools
import sys

import click

from cli import app, pass_ctx
from cli.client import CliConnectionError, CliAuthError, CliNotFoundError


def handle_errors(fn):
    """Decorator that maps client errors to styled output and exit codes."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        # The first positional arg after Click decorators is the context
        ctx_obj = args[0] if args else kwargs.get("ctx")
        try:
            return fn(*args, **kwargs)
        except CliConnectionError as exc:
            ctx_obj.fmt.echo_error(str(exc), "CONNECTION_ERROR")
            raise SystemExit(3)
        except CliAuthError as exc:
            ctx_obj.fmt.echo_error(str(exc), "AUTH_REQUIRED")
            raise SystemExit(4)
        except CliNotFoundError as exc:
            ctx_obj.fmt.echo_error(str(exc), "NOT_FOUND")
            raise SystemExit(2)
        except Exception as exc:
            ctx_obj.fmt.echo_error(str(exc), "GENERAL_ERROR")
            raise SystemExit(1)

    return wrapper


def _read_text_or_stdin(text: str) -> str:
    """Read text from argument or stdin."""
    if text == "-" or (not sys.stdin.isatty() and text == "-"):
        text = sys.stdin.read().strip()
    return text


# ---------------------------------------------------------------------------
# 1. search
# ---------------------------------------------------------------------------

@app.command()
@click.argument("query")
@click.option("-k", "--limit", default=5, type=int, help="Number of results")
@click.option("--hybrid/--no-hybrid", default=True, help="Hybrid search")
@click.option("--threshold", default=None, type=float, help="Similarity threshold")
@click.option("--source", default=None, help="Source prefix filter")
@pass_ctx
@handle_errors
def search(ctx, query, limit, hybrid, threshold, source):
    """Search memories by semantic similarity."""
    data = ctx.client.search(
        query, k=limit, hybrid=hybrid, threshold=threshold, source_prefix=source,
    )

    def human(d):
        results = d.get("results", [])
        if not results:
            click.echo("No results.")
            return
        for r in results:
            sim = r.get("similarity", r.get("rrf_score", r.get("score", 0)))
            pct = f"{sim * 100:.0f}%"
            rid = r.get("id", "?")
            src = r.get("source", "")
            text = r.get("text", "")
            if len(text) > 200:
                text = text[:200] + "..."
            click.secho(f"[{rid}] ({pct}) {src}", fg="cyan")
            click.echo(f"  {text}")

    ctx.fmt.echo(data, human)


# ---------------------------------------------------------------------------
# 2. add
# ---------------------------------------------------------------------------

@app.command()
@click.argument("text", default="-")
@click.option("-s", "--source", required=True, help="Source prefix")
@click.option("--deduplicate/--no-deduplicate", default=True,
              help="Deduplicate before adding")
@pass_ctx
@handle_errors
def add(ctx, text, source, deduplicate):
    """Add a memory. Pass '-' or pipe text via stdin."""
    text = _read_text_or_stdin(text)
    data = ctx.client.add(text, source, deduplicate=deduplicate)

    def human(d):
        mid = d.get("id", "?")
        click.secho(f"Added memory #{mid}", fg="green")

    ctx.fmt.echo(data, human)


# ---------------------------------------------------------------------------
# 3. get
# ---------------------------------------------------------------------------

@app.command()
@click.argument("memory_id")
@pass_ctx
@handle_errors
def get(ctx, memory_id):
    """Get a memory by ID."""
    data = ctx.client.get_memory(memory_id)

    def human(d):
        click.secho(f"ID: {d.get('id', '?')}", fg="cyan")
        click.echo(f"Source: {d.get('source', '')}")
        click.echo(f"Created: {d.get('created_at', '')}")
        click.echo(f"Text: {d.get('text', '')}")

    ctx.fmt.echo(data, human)


# ---------------------------------------------------------------------------
# 4. list
# ---------------------------------------------------------------------------

@app.command("list")
@click.option("--source", default=None, help="Source prefix filter")
@click.option("--offset", default=0, type=int, help="Offset")
@click.option("--limit", default=20, type=int, help="Limit")
@pass_ctx
@handle_errors
def list_cmd(ctx, source, offset, limit):
    """List memories."""
    data = ctx.client.list_memories(offset, limit, source)

    def human(d):
        memories = d.get("memories", [])
        total = d.get("total", len(memories))
        click.echo(f"Showing {len(memories)}/{total} memories")
        for m in memories:
            mid = m.get("id", "?")
            src = m.get("source", "")
            text = m.get("text", "")
            if len(text) > 80:
                text = text[:80] + "..."
            click.echo(f"  [{mid}] {src}  {text}")

    ctx.fmt.echo(data, human)


# ---------------------------------------------------------------------------
# 5. delete
# ---------------------------------------------------------------------------

@app.command()
@click.argument("memory_id")
@pass_ctx
@handle_errors
def delete(ctx, memory_id):
    """Delete a memory by ID."""
    data = ctx.client.delete_memory(memory_id)

    def human(d):
        click.secho(f"Deleted memory #{memory_id}", fg="yellow")

    ctx.fmt.echo(data, human)


# ---------------------------------------------------------------------------
# 6. count
# ---------------------------------------------------------------------------

@app.command()
@click.option("--source", default=None, help="Source prefix filter")
@pass_ctx
@handle_errors
def count(ctx, source):
    """Count memories."""
    data = ctx.client.count(source=source)

    def human(d):
        n = d.get("count", 0)
        click.echo(f"{n} memories")

    ctx.fmt.echo(data, human)


# ---------------------------------------------------------------------------
# 7. upsert
# ---------------------------------------------------------------------------

@app.command()
@click.argument("text", default="-")
@click.option("-s", "--source", required=True, help="Source prefix")
@click.option("-k", "--key", required=True, help="Dedup key")
@pass_ctx
@handle_errors
def upsert(ctx, text, source, key):
    """Insert or update a memory by key."""
    text = _read_text_or_stdin(text)
    data = ctx.client.upsert(text, source, key)

    def human(d):
        mid = d.get("id", "?")
        action = "Updated" if d.get("updated", False) else "Created"
        click.secho(f"{action} memory #{mid}", fg="green")

    ctx.fmt.echo(data, human)


# ---------------------------------------------------------------------------
# 8. is-novel
# ---------------------------------------------------------------------------

@app.command("is-novel")
@click.argument("text", default="-")
@click.option("--threshold", default=0.88, type=float,
              help="Novelty threshold")
@pass_ctx
@handle_errors
def is_novel(ctx, text, threshold):
    """Check if text is novel compared to existing memories."""
    text = _read_text_or_stdin(text)
    data = ctx.client.is_novel(text, threshold=threshold)

    def human(d):
        novel = d.get("is_novel", d.get("novel", True))
        if novel:
            click.secho("Novel", fg="green")
        else:
            click.secho("Not novel", fg="yellow")
            most_similar = d.get("most_similar")
            if most_similar:
                sim = most_similar.get("similarity", most_similar.get("score", 0))
                text_preview = most_similar.get("text", "")
                if len(text_preview) > 120:
                    text_preview = text_preview[:120] + "..."
                click.echo(f"  Most similar ({sim * 100:.0f}%): {text_preview}")

    ctx.fmt.echo(data, human)


# ---------------------------------------------------------------------------
# 9. folders
# ---------------------------------------------------------------------------

@app.command()
@pass_ctx
@handle_errors
def folders(ctx):
    """List source folders with counts."""
    data = ctx.client.folders()

    def human(d):
        folder_list = d.get("folders", [])
        if not folder_list:
            click.echo("No folders.")
            return
        # Find max count width for right-alignment
        max_count = max(f.get("count", 0) for f in folder_list)
        width = len(str(max_count))
        total = 0
        for f in folder_list:
            cnt = f.get("count", 0)
            name = f.get("folder", f.get("name", f.get("prefix", "?")))
            click.echo(f"  {cnt:>{width}}  {name}")
            total += cnt
        click.echo(f"  {total:>{width}}  (total)")

    ctx.fmt.echo(data, human)
