# ADR: Extraction Model Fine-Tuning — Feasibility & Decision

**Date:** 2026-03-27
**Status:** Parked — revisit when organic training data reaches 2,000+ pairs
**Context:** Eval of local Ollama models for extraction showed qwen3:8b at 0.940 quality (vs Haiku 0.958). No extraction-specific model exists in the market. Explored fine-tuning a small model as a potential moat.

---

## The Opportunity

No model in the market is purpose-built for conversational memory extraction. What exists:

| Category | Examples | Why it doesn't fit |
|----------|----------|-------------------|
| Template extraction | NuExtract 2.0 | Schema-filling ("extract name, date, amount"). Expects a template. Returns 0 facts on our open-ended prompt. |
| NER models | spaCy, GLiNER | Entity recognition (person, org, date). Not fact synthesis or categorization. |
| Summarization | BART, T5 | Compress text. Don't categorize, don't apply durability tests, don't output structured JSON. |
| General LLMs | qwen3:8b, Haiku | Follow the extraction prompt well but waste 90% of capacity on abilities we don't use. |

Our extraction task is novel:
1. Takes **conversational text** (not documents or forms)
2. Extracts **durable, categorized facts** (DECISION / LEARNING / DETAIL)
3. Applies a **30-day test** ("would this still be useful?")
4. **Filters noise** (task status, commit hashes, counts, greetings)
5. Outputs **structured JSON arrays** with category labels

A fine-tuned small model (1.7B-4B) specialized for this task could:
- Beat general-purpose 8B models on extraction quality
- Run in <1s inference (vs 4.5s for qwen3:8b)
- Use ~1-2GB VRAM (vs 5GB for qwen3:8b)
- Become a product differentiator no competitor has

---

## Feasibility Analysis

### Compute: Feasible

| Requirement | M4 Pro 24GB | Notes |
|-------------|-------------|-------|
| LoRA fine-tune qwen3:1.7b | ~4GB VRAM, ~30 min | MLX native on Apple Silicon |
| LoRA fine-tune qwen3:4b | ~8GB VRAM, ~60 min | Comfortable fit |
| Full fine-tune qwen3:1.7b | ~12GB VRAM, ~2 hr | Possible but LoRA is sufficient |
| GGUF conversion | CPU-only, ~5 min | Standard tooling (llama.cpp) |
| Ollama custom model | Trivial | `ollama create` with Modelfile |

**Toolchain:** `mlx-lm` for training, `llama.cpp` for GGUF conversion, Ollama for serving.

### Training Data: The Bottleneck

#### What we have

- **2,774 extraction runs** tracked in usage.db
- **~9,500 stored memories** (the output side)
- **0 input/output pairs** — the extraction pipeline discards input text after processing

The audit log records that an extraction happened and its source prefix, but not the conversation text that was fed in. The extracted facts are stored as memories, but without the corresponding input, they can't be used as training data.

#### What we need

For a narrow structured-output task like ours:

| Dataset size | Expected outcome |
|-------------|-----------------|
| 500 pairs | Marginal improvement over prompt engineering. Likely underfits. |
| 1,000-2,000 pairs | Decent LoRA fine-tune. Handles common patterns well. |
| 3,000-5,000 pairs | Reliably beats general-purpose 8B on this specific task. |
| 10,000+ pairs | Robust edge-case handling. Production-grade. |

Each "pair" is: `(conversation_text, extracted_facts_json)` where the extracted facts have been validated (human-approved or quality-checked).

#### Synthetic data limitations

Using Sonnet to generate training data for a smaller model is distillation, not specialization:

1. **Ceiling effect** — the fine-tuned model can't exceed Sonnet's extraction quality, and typically reaches ~85-90% of the teacher model's performance
2. **Distribution mismatch** — synthetic conversations won't match real extraction traffic (hook-triggered session ends, pre-compact contexts, manual extractions)
3. **Bias propagation** — Sonnet's blind spots become the fine-tuned model's blind spots
4. **Prompt artifacts** — synthetic data tends to be "cleaner" than real data, so the model underperforms on messy real-world input

Synthetic data is useful for bootstrapping but has a ceiling around where qwen3:8b already sits with prompt engineering (0.94). To meaningfully surpass that, you need organic data.

---

## The Honest Math

| Approach | Quality | Effort | Time to results |
|----------|---------|--------|----------------|
| **qwen3:8b + prompt (current)** | 0.940 | Done | Done |
| **Fine-tune on 500 synthetic pairs** | ~0.93-0.95 | 1-2 days | 1 week |
| **Fine-tune on 2,000 organic pairs** | ~0.96-0.98 | Passive collection | 3-4 months |
| **Fine-tune on 5,000+ organic pairs** | ~0.97-0.99 | Passive collection | 8-12 months |

The 0.940 → 0.95 improvement from synthetic fine-tuning is marginal and uncertain. The 0.940 → 0.97+ from organic fine-tuning is real but requires months of data collection.

---

## Decision: Park It

### What to do now

1. **Use qwen3:8b with prompt engineering** — 0.940 quality is production-ready
2. **Add passive training data collection** — save (input, output) pairs during extraction (~20 lines of code). Zero runtime cost. Accumulates silently.
3. **Revisit when organic data reaches 2,000 pairs** — roughly 3-4 months at current extraction volume (~700 extractions/month)

### What NOT to do

- Don't start a fine-tuning project now — insufficient data, uncertain ROI
- Don't generate large synthetic datasets — ceiling is too close to what prompt engineering already achieves
- Don't mix fine-tuning infrastructure into the memories codebase — if pursued, it's a separate project/repo

### Training data collection spec

Add to the extraction pipeline in `llm_extract.py`:

```
When EXTRACT_TRAINING_DATA_DIR is set:
  - After extract_facts() returns successfully (facts > 0):
    - Write a JSONL line to {dir}/extraction-training-{date}.jsonl:
      {"input": messages_text, "output": facts_json, "source": source, "context": context, "ts": iso_timestamp}
  - After run_audn() returns:
    - Append AUDN decisions to the same record (for future AUDN fine-tuning)
  - No filtering — collect everything, curate later
  - Max file size: rotate at 50MB
```

This is the only action item. Everything else is deferred.

---

## If We Revisit: The Full Plan

When organic data reaches 2,000+ pairs, the fine-tuning project would be:

### Separate repository

```
memories-extraction-model/
  data/
    raw/                  # JSONL from production collection
    curated/              # Cleaned, validated pairs
    splits/               # train/val/test
  training/
    config.yaml           # Model, LoRA rank, learning rate, epochs
    train.py              # MLX-based training script
    evaluate.py           # Run extraction eval against test split
  models/
    checkpoints/          # Training checkpoints
    exported/             # GGUF files for Ollama
  Modelfile               # Ollama model definition
```

### Training pipeline

1. **Data curation** — filter low-quality pairs (extraction errors, empty outputs), deduplicate similar inputs, stratify by source prefix and context type
2. **Train/val/test split** — 80/10/10, stratified by category distribution
3. **LoRA fine-tune** — rank 16-32, learning rate 1e-4, 3-5 epochs on qwen3:1.7b or 4b base
4. **Evaluation** — run the extraction eval harness (eval/run_extraction_eval.py) against the test split
5. **Export** — convert to GGUF Q4_K_M, create Ollama Modelfile, `ollama create memories-extract`
6. **A/B test** — run both qwen3:8b and the fine-tuned model in production, compare extraction quality metrics

### Success criteria

- Extraction quality >= 0.96 (beating qwen3:8b's 0.940)
- Inference latency < 1.5s (beating qwen3:8b's 4.5s)
- JSON compliance 100%
- Correct noise filtering (0 facts for trivial conversations)
- No regressions on any category (DECISION, LEARNING, DETAIL)

### Cost of NOT doing this

The current qwen3:8b setup is 1.9% behind Haiku and costs $0/month. The risk of not fine-tuning is purely competitive — if a competitor ships an extraction-specific model, our general-purpose approach loses the edge. But today, nobody has one. The market gap is real but not urgent.

---

## References

- Extraction eval results: `eval/results/EXTRACTION-MODEL-EVAL-SUMMARY.md`
- Full comparison data: `eval/results/extraction-eval-full-comparison.json`
- Scenario eval data: `eval/results/scenario-extraction-*.json`
- Extraction pipeline: `llm_extract.py`
- OllamaProvider fix: `llm_provider.py` (2026-03-27, `format:"json"` removal + `think:false`)
- LLMStructBench paper (Feb 2026): arxiv.org/html/2602.14743v1
- NuExtract 2.0: numind.ai/blog/outclassing-frontier-llms----nuextract-2-0-takes-the-lead-in-information-extraction
