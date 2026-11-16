import os

# Most tests read the files or the BM25 index right after a write, so they
# run with saves at once. tests/test_deferred_persist.py covers the delay.
os.environ.setdefault("MEMORIES_PERSIST_DELAY_SEC", "0")
