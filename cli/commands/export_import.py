"""Export and import CLI commands."""

import json
import sys
from pathlib import Path

import click

from cli import app, pass_ctx
from cli.commands.core import handle_errors


@app.command("export")
@click.option("-o", "--output", default=None, type=click.Path(),
              help="Output file path (default: stdout)")
@click.option("--source", default=None, help="Source prefix filter")
@click.option("--since", default=None, help="Export memories created after (ISO8601)")
@click.option("--until", default=None, help="Export memories created before (ISO8601)")
@pass_ctx
@handle_errors
def export_cmd(ctx, output, source, since, until):
    """Export memories to NDJSON file."""
    lines = list(ctx.client.export_stream(source=source, since=since, until=until))

    if output:
        path = Path(output)
        with open(path, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")

        header = json.loads(lines[0]) if lines else {}
        count = header.get("count", len(lines) - 1)
        data = {"file": str(path), "count": count}

        def human(d):
            click.secho(f"Exported {d['count']} memories to {d['file']}", fg="green")

        ctx.fmt.echo(data, human)
    else:
        # Stdout mode — write lines directly if human, wrap in envelope if JSON
        if ctx.fmt.is_json:
            header = json.loads(lines[0]) if lines else {}
            count = header.get("count", len(lines) - 1)
            ctx.fmt.echo({"count": count, "lines": len(lines)})
        else:
            for line in lines:
                click.echo(line)


@app.command("import")
@click.argument("file", default="-")
@click.option("--strategy", default="add",
              type=click.Choice(["add", "smart", "smart+extract"]),
              help="Import strategy")
@click.option("--source-remap", default=None,
              help="Remap source prefix (format: old=new)")
@click.option("--no-backup", is_flag=True, help="Skip auto-backup before import")
@pass_ctx
@handle_errors
def import_cmd(ctx, file, strategy, source_remap, no_backup):
    """Import memories from NDJSON file."""
    if file == "-" or (file is None and not sys.stdin.isatty()):
        raw = sys.stdin.read()
    else:
        path = Path(file)
        if not path.exists():
            raise click.UsageError(f"File not found: {file}")
        raw = path.read_text(encoding="utf-8")

    lines = [line for line in raw.strip().split("\n") if line.strip()]
    if not lines:
        raise click.UsageError("No data to import")

    data = ctx.client.import_upload(
        lines,
        strategy=strategy,
        source_remap=source_remap,
        no_backup=no_backup,
    )

    def human(d):
        click.secho(f"Imported: {d.get('imported', 0)}", fg="green")
        skipped = d.get("skipped", 0)
        updated = d.get("updated", 0)
        if skipped:
            click.echo(f"Skipped:  {skipped}")
        if updated:
            click.echo(f"Updated:  {updated}")
        errors = d.get("errors", [])
        if errors:
            click.secho(f"Errors:   {len(errors)}", fg="red")
            for e in errors[:5]:
                click.echo(f"  Line {e['line']}: {e['error']}")
        backup = d.get("backup")
        if backup:
            click.echo(f"Backup:   {backup}")

    ctx.fmt.echo(data, human)
