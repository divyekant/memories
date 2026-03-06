"""Extract CLI commands -- submit, status, poll."""

import sys
from pathlib import Path

import click

from cli import app, pass_ctx
from cli.commands.core import handle_errors


def read_file_or_stdin(file_arg):
    """Read transcript from file path or stdin."""
    if file_arg == "-" or (file_arg is None and not sys.stdin.isatty()):
        return sys.stdin.read()
    path = Path(file_arg)
    if not path.exists():
        raise click.UsageError(f"File not found: {file_arg}")
    return path.read_text()


@app.group()
def extract():
    """Memory extraction from transcripts."""


@extract.command("submit")
@click.argument("file", default="-")
@click.option("-s", "--source", required=True, help="Source prefix for extracted memories")
@click.option("--context", default="stop",
              type=click.Choice(["stop", "pre_compact", "session_end", "after_agent"]),
              help="Extraction context")
@pass_ctx
@handle_errors
def extract_submit(ctx, file, source, context):
    """Submit a transcript for memory extraction.

    Pass a file path or '-' to read from stdin.
    """
    raw = read_file_or_stdin(file)
    if not raw or not raw.strip():
        raise click.UsageError("No transcript provided")
    data = ctx.client.extract_submit(raw.strip(), source, context)

    def human(d):
        job_id = d.get("job_id", d.get("id", "?"))
        status = d.get("status", "submitted")
        click.secho(f"Job {job_id}: {status}", fg="cyan")

    ctx.fmt.echo(data, human)


@extract.command("status")
@click.argument("job_id", required=False, default=None)
@pass_ctx
@handle_errors
def extract_status(ctx, job_id):
    """Check extraction job status or system status."""
    if job_id:
        data = ctx.client.extract_status(job_id)
    else:
        data = ctx.client.extract_system_status()

    def human(d):
        if job_id:
            status = d.get("status", "unknown")
            jid = d.get("job_id", d.get("id", job_id))
            click.echo(f"Job {jid}: {status}")
            result = d.get("result", d.get("memories"))
            if result:
                if isinstance(result, list):
                    click.echo(f"  Extracted {len(result)} memories")
                else:
                    click.echo(f"  Result: {result}")
        else:
            for key, value in d.items():
                click.echo(f"{key}: {value}")

    ctx.fmt.echo(data, human)


@extract.command("poll")
@click.argument("job_id")
@click.option("--wait", is_flag=True, help="Block until job completes")
@click.option("--timeout", default=120, type=int, help="Timeout in seconds (with --wait)")
@pass_ctx
@handle_errors
def extract_poll(ctx, job_id, wait, timeout):
    """Poll an extraction job for completion."""
    if wait:
        data = ctx.client.extract_poll(job_id, timeout=timeout)
    else:
        data = ctx.client.extract_status(job_id)

    def human(d):
        status = d.get("status", "unknown")
        jid = d.get("job_id", d.get("id", job_id))
        click.echo(f"Job {jid}: {status}")
        if status == "completed":
            result = d.get("result", d.get("memories"))
            if isinstance(result, list):
                click.secho(f"  Extracted {len(result)} memories", fg="green")
            elif result:
                click.echo(f"  Result: {result}")
        elif status == "failed":
            error = d.get("error", "unknown error")
            click.secho(f"  Error: {error}", fg="red")

    ctx.fmt.echo(data, human)
