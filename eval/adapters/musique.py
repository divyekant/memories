"""MuSiQue dataset adapter for graph eval.

Isolated mode: each window gets its own passages + links.
Supports 2-hop, 3-hop, and 4-hop questions with explicit decomposition chains.
"""

import json
from typing import Any

from .base import DatasetAdapter

PREFIX = "eval/musique"


class MuSiQueAdapter(DatasetAdapter):
    """MuSiQue adapter — isolated mode, per-window passages + links."""

    def __init__(self, min_hops: int = 0):
        self.min_hops = min_hops

    @property
    def mode(self) -> str:
        return "isolated"

    def load_questions(self, path: str, max_questions: int = 0, **kwargs) -> list[dict]:
        questions = []
        with open(path) as f:
            for line in f:
                q = json.loads(line)
                if not q.get("answerable", True):
                    continue
                decomp = q.get("question_decomposition", [])
                if isinstance(decomp, str):
                    decomp = json.loads(decomp) if decomp else []
                    q["question_decomposition"] = decomp
                paras = q.get("paragraphs", [])
                if isinstance(paras, str):
                    paras = json.loads(paras) if paras else []
                    q["paragraphs"] = paras
                if self.min_hops > 0 and len(decomp) < self.min_hops:
                    continue
                questions.append(q)
        if max_questions > 0:
            questions = questions[:max_questions]
        return questions

    def seed_window(self, client, questions: list[dict], corpus_ids: dict) -> dict:
        """Seed passages + links for a window of questions."""
        id_maps = {}

        for q in questions:
            qid = q["id"]
            paras = q["paragraphs"]
            decomp = q["question_decomposition"]

            # Batch seed paragraphs
            batch = [{"text": p["paragraph_text"], "source": f"{PREFIX}/{qid}/{p['title'][:50]}"}
                     for p in paras]
            r = client.post("/memory/add-batch", json={"memories": batch, "deduplicate": False})
            r.raise_for_status()
            batch_ids = r.json().get("ids", [])

            id_map = {}
            for i, para in enumerate(paras):
                if i < len(batch_ids):
                    id_map[para["idx"]] = batch_ids[i]
            id_maps[qid] = id_map

            # Create links along hop chain
            supporting = [d["paragraph_support_idx"] for d in decomp]
            for i in range(len(supporting) - 1):
                from_idx, to_idx = supporting[i], supporting[i + 1]
                if from_idx in id_map and to_idx in id_map:
                    client.post(f"/memory/{id_map[from_idx]}/link",
                                json={"to_id": id_map[to_idx], "type": "related_to"})

        return id_maps

    def cleanup_window(self, client, questions: list[dict], corpus_ids: dict):
        """Delete all memories for this window's questions."""
        for q in questions:
            client.post("/memory/delete-by-prefix", json={"source_prefix": f"{PREFIX}/{q['id']}"})

    def search_scope(self, question: dict) -> str:
        return f"{PREFIX}/{question['id']}"

    def score(self, question: dict, results_off: list, results_on: list, id_map: dict) -> dict:
        answer = question["answer"]
        decomp = question["question_decomposition"]
        n_hops = len(decomp)

        supporting_indices = [d["paragraph_support_idx"] for d in decomp]
        supporting_ids = set(id_map.get(idx) for idx in supporting_indices if idx in id_map)

        def has_answer(res_list):
            return answer.lower() in " ".join(r.get("text", "") for r in res_list).lower()

        def support_recall(res_list):
            found = sum(1 for r in res_list if r["id"] in supporting_ids)
            return found / len(supporting_ids) if supporting_ids else 0

        def answer_rank(res_list):
            for i, r in enumerate(res_list):
                if answer.lower() in r.get("text", "").lower():
                    return i + 1
            return -1

        hit_off = has_answer(results_off)
        hit_on = has_answer(results_on)

        off_ids = {r["id"] for r in results_off}
        has_support_off = bool(off_ids & supporting_ids)
        conditional_candidate = has_support_off and not hit_off

        return {
            "qid": str(question["id"]),
            "n_hops": n_hops,
            "question": question["question"],
            "answer": answer,
            "hit_off": hit_off,
            "hit_on": hit_on,
            "delta": int(hit_on) - int(hit_off),
            "support_recall_off": round(support_recall(results_off), 3),
            "support_recall_on": round(support_recall(results_on), 3),
            "answer_rank_off": answer_rank(results_off),
            "answer_rank_on": answer_rank(results_on),
            "conditional_candidate": conditional_candidate,
            "conditional_rescued": conditional_candidate and hit_on,
            "graph_only": sum(1 for r in results_on if r.get("match_type") == "graph"),
            "boosted": sum(1 for r in results_on if r.get("match_type") == "direct+graph"),
        }
