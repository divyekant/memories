"""Seed corpus for the parity cases.

The corpus is synthetic and comes from a fixed random seed. It goes into the
server through the HTTP API, so a replay against a different server makes the
same corpus. It has records in many source prefixes, links, pinned records,
archived records and conflict markers.
"""

import random

from eval.generate_synthetic_memories import generate_all

SEED = 20261116
COUNT = 240
BATCH = 80
LINK_TYPES = ["related_to", "related_to", "related_to", "supersedes", "blocked_by", "caused_by", "reinforces"]
# Top-level prefixes of the clients, so source scope and folder routes see a real spread.
PREFIXES = ["claude-code/voltis", "codex/voltis", "learning/voltis", "wip/voltis", "eval/graph/synth"]
ADMIN = {"X-API-Key": "{{key:admin}}"}


def memories():
    rnd = random.Random(SEED)
    out = []
    for m in generate_all(COUNT):
        tail = m["source"].split("eval/graph/synth/", 1)[-1].strip("/") or "misc"
        out.append({"text": m["text"], "source": f"{rnd.choice(PREFIXES)}/{tail}"})
    return out


def _case(name, method, path, route, json_body=None, save=None):
    request = {"method": method, "path": path, "headers": dict(ADMIN)}
    if json_body is not None:
        request["json"] = json_body
    c = {"name": name, "route": route, "rules": [], "request": request}
    if save:
        c["save"] = save
    return c


def build():
    rnd = random.Random(SEED + 1)
    mems = memories()
    cases = []
    batches = [mems[i:i + BATCH] for i in range(0, len(mems), BATCH)]
    for n, batch in enumerate(batches):
        cases.append(_case(f"seed-add-batch-{n}", "POST", "/memory/add-batch", "POST /memory/add-batch",
                           {"memories": batch}, save={f"seed{n}": "ids"}))

    def ref(i):
        return f"{{{{seed{i // BATCH}.{i % BATCH}}}}}"

    for n in range(30):
        a, b = rnd.sample(range(len(mems)), 2)
        cases.append(_case(f"seed-link-{n}", "POST", f"/memory/{ref(a)}/link", "POST /memory/{memory_id}/link",
                           {"to_id": ref(b), "type": rnd.choice(LINK_TYPES)}))
    picked = rnd.sample(range(len(mems)), 16)
    for n, i in enumerate(picked[:8]):
        cases.append(_case(f"seed-pin-{n}", "PATCH", f"/memory/{ref(i)}", "PATCH /memory/{memory_id}",
                           {"pinned": True}))
    for n, i in enumerate(picked[8:]):
        cases.append(_case(f"seed-archive-{n}", "PATCH", f"/memory/{ref(i)}", "PATCH /memory/{memory_id}",
                           {"archived": True}))
    for n in range(6):
        i = rnd.randrange(len(mems))
        # Half of the markers stay in the source of the target, half cross to a new source.
        source = mems[i]["source"] if n % 2 == 0 else "claude-code/voltis/conflicts"
        cases.append(_case(f"seed-conflict-{n}", "POST", "/memory/add", "POST /memory/add",
                           {"text": f"Correction {n}: {mems[i]['text']} This changed in 2026.",
                            "source": source, "metadata": {"conflicts_with": ref(i)}},
                           save={f"conflict{n}": "id"}))
    return cases
