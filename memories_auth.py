"""Memories CLI auth tool — one-time OAuth setup for extraction providers.

Usage:
    python -m memories auth chatgpt   # Interactive ChatGPT OAuth flow
    python -m memories auth status    # Show current provider config
"""
import os
import sys
import json
import logging
import threading
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger(__name__)

DEFAULT_ENV_PATH = Path.home() / ".config" / "memories" / "env"
DEFAULT_CALLBACK_PORT = 9876

# Keys managed by auth setup — removed when switching providers
_PROVIDER_KEYS = {
    "EXTRACT_PROVIDER", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
    "CHATGPT_REFRESH_TOKEN", "CHATGPT_CLIENT_ID", "OLLAMA_URL",
    "EXTRACT_MODEL",
}


def write_env_file(
    env_path: Path,
    provider: str,
    refresh_token: str | None = None,
    client_id: str | None = None,
    api_key: str | None = None,
) -> None:
    """Write or update the env file, preserving non-provider vars."""
    env_path.parent.mkdir(parents=True, exist_ok=True)
    existing_lines: list[str] = []
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                existing_lines.append(line)
                continue
            key = stripped.split("=", 1)[0].strip()
            if key not in _PROVIDER_KEYS:
                existing_lines.append(line)

    new_lines = [f'EXTRACT_PROVIDER="{provider}"']
    if refresh_token:
        new_lines.append(f'CHATGPT_REFRESH_TOKEN="{refresh_token}"')
    if client_id:
        new_lines.append(f'CHATGPT_CLIENT_ID="{client_id}"')
    if api_key:
        if provider == "anthropic":
            new_lines.append(f'ANTHROPIC_API_KEY="{api_key}"')
        elif provider == "openai":
            new_lines.append(f'OPENAI_API_KEY="{api_key}"')

    all_lines = existing_lines + new_lines
    env_path.write_text("\n".join(all_lines) + "\n")


def get_auth_status() -> dict:
    """Read current provider config from environment and return status dict."""
    provider = os.environ.get("EXTRACT_PROVIDER", "").strip() or None
    if not provider:
        return {"provider": None, "configured": False}

    status = {"provider": provider, "configured": True}

    if provider == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if key:
            status["key_preview"] = key[:12] + "****" if len(key) > 12 else "****"
        else:
            status["configured"] = False

    elif provider == "openai":
        key = os.environ.get("OPENAI_API_KEY", "")
        if key:
            status["key_preview"] = key[:7] + "****" if len(key) > 7 else "****"
        else:
            status["configured"] = False

    elif provider == "chatgpt-subscription":
        rt = os.environ.get("CHATGPT_REFRESH_TOKEN", "")
        cid = os.environ.get("CHATGPT_CLIENT_ID", "")
        if rt and cid:
            status["key_preview"] = rt[:8] + "****"
            status["client_id"] = cid
        else:
            status["configured"] = False

    elif provider == "ollama":
        url = os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434")
        status["ollama_url"] = url

    model = os.environ.get("EXTRACT_MODEL", "")
    if model:
        status["model"] = model

    return status


def run_chatgpt_auth(client_id: str, port: int = DEFAULT_CALLBACK_PORT) -> dict:
    """Run the interactive ChatGPT OAuth flow. Returns tokens dict."""
    from chatgpt_oauth import (
        generate_code_verifier,
        compute_code_challenge,
        generate_state,
        build_authorize_url,
        exchange_code_for_tokens,
        exchange_id_token_for_api_key,
    )

    code_verifier = generate_code_verifier()
    code_challenge = compute_code_challenge(code_verifier)
    state = generate_state()
    redirect_uri = f"http://localhost:{port}/callback"

    auth_url = build_authorize_url(
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        state=state,
    )

    result = {"code": None, "error": None}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)

            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return

            if params.get("error"):
                result["error"] = params["error"][0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<h2>Auth failed. You can close this tab.</h2>")
                threading.Thread(target=self.server.shutdown).start()
                return

            received_state = params.get("state", [""])[0]
            if received_state != state:
                result["error"] = "state_mismatch"
                self.send_response(400)
                self.end_headers()
                return

            result["code"] = params.get("code", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h2>Success! You can close this tab.</h2>")
            threading.Thread(target=self.server.shutdown).start()

        def log_message(self, format, *args):
            pass  # Suppress HTTP logs

    server = HTTPServer(("127.0.0.1", port), CallbackHandler)

    print(f"\nOpening browser for ChatGPT login...")
    print(f"If the browser doesn't open, visit:\n  {auth_url}\n")
    webbrowser.open(auth_url)

    print("Waiting for callback...")
    server.serve_forever()
    server.server_close()

    if result["error"]:
        raise RuntimeError(f"OAuth failed: {result['error']}")
    if not result["code"]:
        raise RuntimeError("No authorization code received")

    print("Exchanging authorization code for tokens...")
    tokens = exchange_code_for_tokens(
        code=result["code"],
        code_verifier=code_verifier,
        redirect_uri=redirect_uri,
        client_id=client_id,
    )

    print("Exchanging id_token for OpenAI API key...")
    api_key = exchange_id_token_for_api_key(
        id_token=tokens["id_token"],
        client_id=client_id,
    )

    return {
        "refresh_token": tokens.get("refresh_token", ""),
        "id_token": tokens.get("id_token", ""),
        "api_key": api_key,
        "expires_in": tokens.get("expires_in"),
    }


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="memories auth",
        description="Memories extraction provider auth setup",
    )
    sub = parser.add_subparsers(dest="command")

    chatgpt_parser = sub.add_parser("chatgpt", help="Set up ChatGPT subscription auth")
    chatgpt_parser.add_argument("--client-id", required=True, help="OpenAI OAuth client ID")
    chatgpt_parser.add_argument("--port", type=int, default=DEFAULT_CALLBACK_PORT, help="Callback port")
    chatgpt_parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_PATH, help="Env file path")

    sub.add_parser("status", help="Show current provider configuration")

    args = parser.parse_args(argv)

    if args.command == "chatgpt":
        try:
            result = run_chatgpt_auth(client_id=args.client_id, port=args.port)
            write_env_file(
                env_path=args.env_file,
                provider="chatgpt-subscription",
                refresh_token=result["refresh_token"],
                client_id=args.client_id,
            )
            print(f"\n\u2713 Auth complete! Config written to {args.env_file}")
            print(f"  Provider: chatgpt-subscription")
            print(f"  API key:  {result['api_key'][:12]}...")
            print(f"\nRestart Memories to use the new provider.")
        except Exception as e:
            print(f"\n\u2717 Auth failed: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "status":
        status = get_auth_status()
        if not status["configured"]:
            print("No extraction provider configured.")
            print("Set EXTRACT_PROVIDER env var or run: python -m memories auth chatgpt")
        else:
            print(f"Provider:  {status['provider']}")
            if "key_preview" in status:
                print(f"Key:       {status['key_preview']}")
            if "model" in status:
                print(f"Model:     {status['model']}")
            if "ollama_url" in status:
                print(f"URL:       {status['ollama_url']}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
