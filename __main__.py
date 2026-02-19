"""Entry point for `python -m memories`."""
import sys

if len(sys.argv) >= 2 and sys.argv[1] == "auth":
    from memories_auth import main
    main(sys.argv[2:])
else:
    print("Usage: python -m memories auth [chatgpt|status]")
    sys.exit(1)
