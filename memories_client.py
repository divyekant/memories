"""Memories Python Client SDK.

Standalone HTTP client for the Memories API. Can be used independently
of the CLI — just import and use:

    from memories_client import MemoriesClient

    client = MemoriesClient("http://localhost:8900", api_key="your-key")
    results = client.search("what did we decide about auth?")
    client.add("We chose JWT tokens for auth", source="claude-code/myapp")
"""

from cli.client import (
    MemoriesClient,
    CliConnectionError as ConnectionError,
    CliAuthError as AuthError,
    CliNotFoundError as NotFoundError,
    CliServerError as ServerError,
)

__all__ = [
    "MemoriesClient",
    "ConnectionError",
    "AuthError",
    "NotFoundError",
    "ServerError",
]
