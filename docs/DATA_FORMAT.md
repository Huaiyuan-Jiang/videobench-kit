# Public data format

Everything the leaderboard needs is in `data/`. Only fields the site shows are kept. JSON keys are sorted.

## data/summary.json

```json
{
  "schema_version": 2,
  "roles":  [{"id": "SORT", "label": "Footage Logging"}],
  "tasks":  [{"id": "SORT1", "role": "SORT", "difficulty": "easy", "reference_only": false}],
  "probes": [{"id": "P1", "has_answer": false, "measures": ["first_event_s", "total_s"]}],
  "models": [...],
  "runs": [...],
  "probe_runs": [...]
}
```

### models[]

| Field | Meaning |
|---|---|
| `id`, `label`, `vendor`, `color_key` | name, maker, logo and color key |
| `kind` | always `examinee` |
| `card` | `tool`, `channel`, `channel_en`, `model`, `effort`, `fast`, `image_input`, `video_input`, `context_tokens` |
| `coverage` | `tasks_done`, `tasks_total`, `probes_done` |
| `overall_score`, `overall_score_nospeed` | totals, 0 to 100 |
| `overall_status` | `ok` when the totals are complete |
| `role_scores[]` | `role`, `index`, `index_nospeed`, `coverage`, `total`, `passed` |

### runs[]

One row per counted task run.

| Field | Meaning |
|---|---|
| `model`, `task`, `role`, `repeat`, `counted` | which run |
| `status` | `ok`, `late`, `timeout`, `error`, `void`, `unsupported` |
| `score`, `score_nospeed` | task score with and without speed |
| `zero` | null, or `cheat`, `timeout`, `no_submit`, `error`, `lie` |
| `passed` | Q reached the pass line |
| `metrics` | `wall_s`, `tokens_in`, `tokens_out`, `tokens_cache_read`; `tokens_source` is `【推算】` when token counts were estimated |

### probe_runs[]

`model`, `probe`, `n` (runs), `correct` (runs answered right, when the probe has an answer) and `median`, which holds the median of each measure:

| Measure | Meaning |
|---|---|
| `first_event_s` | seconds to the first event of any kind |
| `first_text_s` | seconds to the first text event |
| `first_stream_chunk_s` | seconds to the first streamed chunk, where the channel logs it |
| `output_rate_chars_per_s`, `output_rate_tokens_per_s` | output size over model time |
| `stream_chars_per_s` | streamed characters over streaming time |
| `per_roundtrip_s` | model time per tool round trip |
| `total_s`, `model_s`, `startup_s` | total time, model time, platform start-up time |

## data/tics.json

`{"models": {"<id>": {"per10k": 12.3}}}`: how often a model's visible reasoning uses filler words like "Wait" or "Hmm", per 10,000 characters. Only models whose logs keep full reasoning text are listed.

## data/reviews/zh/<id>.json, data/reviews/en/<id>.json

| Field | Meaning |
|---|---|
| `id` | model id |
| `one_line` | one-sentence verdict |
| `roles[]` | `role` (role id) and `text` for each role |
| `working_style` | speed, tokens, tool habits |
| `honesty` | lies and rule breaks |
| `dispatch_advice` | `give` and `avoid`: lists of jobs to hand it or not |
| `confidence` | how solid the review is |
