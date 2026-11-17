# Model Compatibility

The `AIModel` enum + the validity matrix for `reasoning_effort` × `verbosity` × model. The platform's `validate_model_effort_verbosity` check rejects incompatible combinations on most extraction, QA, decision, email, and agent configs at save time.

---

## `AIModel` enum

### OpenAI — current
- `gpt-5.1`
- `gpt-5.2`
- `gpt-5.2-codex`
- `gpt-5.4`
- `gpt-5.4-mini`
- `gpt-5.4-nano`
- `gpt-5.5`
- `o3`

### OpenAI — legacy (kept for existing configs; never pick)
- `gpt-4o`, `gpt-4o-2024-05-13`, `gpt-4o-2024-08-06`, `gpt-4o-mini`, `gpt-4o-mini-2024-07-18`

### Google
- `gemini-2.5-flash`
- `gemini-2.5-pro`

### Anthropic
- `claude-3-5-sonnet-20241022`
- `claude-sonnet-4-20250514`
- `claude-sonnet-4-6`
- `claude-sonnet-5`
- `claude-opus-4-20250514`
- `claude-opus-4-6`
- `claude-opus-4-7`
- `claude-opus-4-8`

The builder UI's model picker offers a subset (`gpt-5.5`, `gpt-5.4`, `gpt-5.1`, `o3`, `claude-3-5-sonnet-20241022`; `agentic_extraction` / `agentic_guideline_check` also offer Claude Sonnet 4.6 and Opus 4.6/4.7; the separate `llm_extraction_model` field also offers Gemini 2.5 Flash/Pro). Any enum value written directly into JSON is accepted.

---

## `reasoning_effort` validity matrix

`reasoning_effort` ∈ `{ none, minimal, low, medium, high, xhigh }`; each model accepts only a subset:

| Model | Valid `reasoning_effort` |
|---|---|
| `gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini`, `gpt-5.4-nano`, `gpt-5.2` | `none`, `low`, `medium`, `high`, `xhigh` |
| `gpt-5.2-codex` | `low`, `medium`, `high`, `xhigh` |
| `gpt-5.1` | `none`, `low`, `medium`, `high` (no `xhigh`) |
| `o3` | `low`, `medium`, `high` |
| `gemini-2.5-flash`, `gemini-2.5-pro` | `none`, `low`, `medium`, `high` |
| Claude 4-series + `claude-sonnet-5` (`claude-sonnet-4-*`, `claude-opus-4-*`) | `low`, `medium`, `high`, `xhigh` (`none`/`minimal` are GPT-only) |
| Anything else (`gpt-4o*`, `claude-3-5-sonnet-20241022`) | **must be `null`** |

Setting `reasoning_effort` outside the model's subset → save rejected with `reasoning_effort '<x>' is not valid for model '<y>'`.

On a reasoning-capable model, setting an effort is what turns reasoning on — `"medium"` is the sensible default.

---

## `verbosity` validity

`verbosity` ∈ `{ low, medium, high }`, supported **only** by: `gpt-5.1`, `gpt-5.2`, `gpt-5.5`, `gpt-5.4`, `gpt-5.4-mini`, `gpt-5.4-nano`.

All other models must have `verbosity: null`.

---

## Recommended defaults

Set the model explicitly on every model-bearing step — don't rely on the picker default.

| Use case | Model | Reasoning | Verbosity |
|---|---|---|---|
| Default for extraction / QA / agent steps | `gpt-5.5` | `medium` | `null` |
| High-volume or simple steps (cost-sensitive) | `gpt-5.1` | `low`-`medium` | `null` |
| Document classification | `gpt-5.1` | `low` | `null` |
| Hard / nuanced reasoning | `gpt-5.5` | `high` | `null` |
| Trellis / payout-grade disambiguation | `claude-opus-4-7` | `high` | `null` |
| Web search orchestrator | `gpt-5.5` (`gpt-5.1` for cost) | `medium` | `null` |

- `gpt-5.5` is the strongest and premium-priced; downgrade to `gpt-5.1` where volume is high or the task is simple.
- `gpt-4o` / `gpt-4o-mini` are legacy and being phased out — never pick them for new work.
- `gpt-5.4` is also significantly more expensive than `gpt-5.1` — use only with benchmarked benefit.

---

## Where the check applies

`validate_model_effort_verbosity` runs on most step configs, including: the extraction family (`extract_from_multiple_sources`, `extract_rows_from_multiple_sources`, `extract_mixed_schema_from_multiple_sources`, legacy single-source configs), `classify_documents`, `multi_column_qa`, `generate_qa_table_from_agent` (and legacy `_from_kb`), `submission_summary_generator`, `ofac_agent`, `osha_agent`, `web_search`, `email` (compose model), `trellis_law`, `execution_matching`.

An incompatible (model, reasoning_effort, verbosity) tuple anywhere fails the save.
