"""Make the G2 embedding fixture with the Python reference embedder (v5.19.0).

Run from the repository root:

    uv run python server/crates/memories-embed/tests/fixtures/make_embed_fixture.py

The script writes embed_texts.json (1,000 texts) and embed_vectors.f32
(1,000 x 384 little-endian float32 vectors, normalized) next to itself.
"""

import json
import random
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT))

from onnx_embedder import OnnxEmbedder  # noqa: E402

OUT = Path(__file__).resolve().parent
COUNT = 1000

WORDS = (
    "memory server search vector qdrant rust python deploy docker config hook "
    "extract fact decision bug fix release branch merge test cache token model "
    "user project session thread client route index payload backup restore "
    "the a an of to in for on with by from and or not is was will be "
    "quickly carefully always never before after because unless until"
).split()

UNICODE = [
    "Café crème brûlée à la façon de Zoë",
    "東京の天気は晴れです。メモリを保存しました。",
    "数据库迁移已完成，向量维度为三百八十四。",
    "Привет, мир! Сервер памяти работает.",
    "مرحبا بالعالم، هذا اختبار",
    "שלום עולם",
    "🚀 deploy done ✅ tests green 🎉",
    "naïve résumé coöperate façade",
    "ñandú pingüino cigüeña",
    "Ελληνικά γράμματα αβγδ",
    "한국어 텍스트 임베딩 테스트",
    "emoji family 👨‍👩‍👧‍👦 and flags 🇮🇳🇺🇸",
]

CODE = [
    "def encode(texts): return model.run(texts)",
    "fn main() { println!(\"hello\"); }",
    "SELECT id, text FROM memories WHERE source LIKE 'claude-code/%';",
    "curl -X POST http://localhost:8900/search -d '{\"query\":\"x\"}'",
    "export ORT_DYLIB_PATH=/opt/onnxruntime/lib/libonnxruntime.so",
    "{\"id\": 117374, \"source\": \"wip/memories\", \"archived\": false}",
    "git push -u origin claude/project-thread-960uuq",
    "/data/model-cache/models--sentence-transformers--all-MiniLM-L6-v2",
]

SPECIAL = ["", " ", "\n", "\t\t", "a", "?", "...", "1234567890", "UPPER CASE TEXT", "[CLS] [SEP] [PAD]"]


def sentence(rng: random.Random, n: int) -> str:
    words = [rng.choice(WORDS) for _ in range(n)]
    text = " ".join(words)
    return text[0].upper() + text[1:] + rng.choice([".", "!", "?", ""])


def make_texts() -> list:
    rng = random.Random(20261116)
    texts = list(SPECIAL) + list(UNICODE) + list(CODE)
    while len(texts) < COUNT:
        kind = rng.randrange(6)
        if kind == 0:
            texts.append(sentence(rng, rng.randint(1, 5)))
        elif kind == 1:
            texts.append(sentence(rng, rng.randint(6, 40)))
        elif kind == 2:
            # Longer than 256 tokens, so the cut at 256 tokens applies.
            texts.append(" ".join(sentence(rng, 20) for _ in range(rng.randint(15, 30))))
        elif kind == 3:
            texts.append(f"{rng.choice(UNICODE)} {sentence(rng, rng.randint(3, 12))}")
        elif kind == 4:
            texts.append(f"{sentence(rng, rng.randint(2, 8))} {rng.choice(CODE)}")
        else:
            texts.append(f"Decision {rng.randint(1, 99999)}: {sentence(rng, rng.randint(5, 25))}")
    return texts[:COUNT]


def main() -> None:
    texts = make_texts()
    vectors = OnnxEmbedder("all-MiniLM-L6-v2").encode(texts, normalize_embeddings=True)
    assert vectors.shape == (COUNT, 384), vectors.shape
    (OUT / "embed_texts.json").write_text(
        json.dumps({"model": "all-MiniLM-L6-v2", "dim": 384, "texts": texts}, ensure_ascii=False, indent=0) + "\n",
        encoding="utf-8",
    )
    vectors.astype("<f4").tofile(OUT / "embed_vectors.f32")
    print(f"wrote {len(texts)} texts and vectors to {OUT}")


if __name__ == "__main__":
    main()
