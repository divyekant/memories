"""Auth CLI commands — wraps memories_auth.py functions in Click."""

import click

from cli import app, pass_ctx
from cli.commands.core import handle_errors


@app.group()
def auth():
    """Manage extraction provider authentication."""
    pass


@auth.command()
@click.option("--client-id", required=True, help="OpenAI OAuth client ID")
@click.option("--port", type=int, default=9876, help="Callback port")
@click.option("--env-file", type=click.Path(), default=None, help="Env file path")
@pass_ctx
@handle_errors
def chatgpt(ctx, client_id, port, env_file):
    """Set up ChatGPT subscription auth via OAuth."""
    from memories_auth import run_chatgpt_auth, write_env_file, DEFAULT_ENV_PATH
    from pathlib import Path

    env_path = Path(env_file) if env_file else DEFAULT_ENV_PATH
    result = run_chatgpt_auth(client_id=client_id, port=port)
    write_env_file(
        env_path=env_path,
        provider="chatgpt-subscription",
        refresh_token=result["refresh_token"],
        client_id=client_id,
    )
    data = {"provider": "chatgpt-subscription", "env_file": str(env_path)}
    ctx.fmt.echo(
        data,
        human_fn=lambda d: click.echo(
            f"Auth complete! Config written to {d['env_file']}"
        ),
    )


@auth.command()
@pass_ctx
@handle_errors
def status(ctx):
    """Show current provider configuration."""
    from memories_auth import get_auth_status

    data = get_auth_status()

    def human(d):
        if not d.get("configured"):
            click.echo("No extraction provider configured.")
            return
        click.echo(f"Provider:  {d['provider']}")
        if "key_preview" in d:
            click.echo(f"Key:       {d['key_preview']}")
        if "model" in d:
            click.echo(f"Model:     {d['model']}")
        if "ollama_url" in d:
            click.echo(f"URL:       {d['ollama_url']}")

    ctx.fmt.echo(data, human_fn=human)
