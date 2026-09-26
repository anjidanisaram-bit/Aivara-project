# Aivara Insight Lite

A locally-hosted Python web app that lets a small internal team upload **any
CSV** (arbitrary schema), automatically understands its structure, computes
real analytics locally, and uses an LLM (via OpenRouter, free tier) only to
turn already-computed numbers into short natural-language insights.

**Core design principle: compute first, ask the LLM second.** All arithmetic,
aggregation, trend, and driver analysis happens locally in pandas. The LLM
never receives the raw CSV — it receives a small, pre-computed JSON "facts
packet" and is asked only to narrate it.

See `aivara-insight-lite_product_guide.md` (the original product/build spec)
for the full design rationale, architecture, and phased milestone plan this
implementation follows.

## Features (v1)

- Robust CSV ingestion: unknown delimiter/encoding/header handled automatically.
- Automatic semantic column classification (date / metric / dimension / id / text),
  with manual override in the UI.
- Local computation of KPIs, trend, period-over-period deltas, driver
  decomposition, and simple anomaly/risk flags.
- Auto-generated dashboard: KPI tiles, trend chart, driver breakdown, insight card.
- One AI-generated narrative insight per KPI/view, budget-capped and cached.
- Download the dashboard as a `.pptx` — built entirely locally, no extra LLM
  calls at export time.

## Quickstart (local, no Docker)

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# edit .env and set OPENROUTER_API_KEY=sk-or-...  (get one at https://openrouter.ai/)

streamlit run app.py
```

Then open the URL Streamlit prints (usually `http://localhost:8501`).

## Quickstart (Docker)

```bash
docker build -t aivara-insight-lite .
docker run -p 8501:8501 \
  -e OPENROUTER_API_KEY=sk-or-... \
  aivara-insight-lite
```

Open `http://localhost:8501`.

## Configuration

All configuration is via environment variables (see `.env.example` for the
full list and defaults). Nothing sensitive is hardcoded in `config.py`.

| Variable | Purpose | Default |
|---|---|---|
| `OPENROUTER_API_KEY` | Required. Your OpenRouter API key. | — |
| `AIVARA_MODEL` | Model id used for insight generation. | `openrouter/auto` |
| `AIVARA_FALLBACK_MODEL` | Used if a non-free model id is passed without opting in to paid calls. | `meta-llama/llama-3.1-8b-instruct:free` |
| `AIVARA_DAILY_BUDGET` | Hard ceiling on LLM calls per UTC day. | `30` |
| `AIVARA_MAX_OUTPUT_TOKENS` | Per-call output token cap. | `150` |

v1 has no app-level login system — this app is meant to sit behind your
internal network / VPN, or a shared-secret gate in a reverse proxy in front
of Streamlit. Do not expose it directly to the public internet.

## Running the tests

```bash
pip install pytest
pytest tests/ -v
```

Tests cover: robust ingestion across messy fixture CSVs (semicolon-delimited,
no header, mixed encoding, currency-formatted numbers, no date column),
column classification, KPI math, driver decomposition (against a
hand-computed example), LLM call budget enforcement, and a guarantee that
PPTX export never triggers a network call.

## Repository layout

```
aivara-insight-lite/
├── app.py                  # Streamlit entrypoint — routing/tabs only
├── config.py                # env/secrets, budget constants, model list
├── ingestion/                # CSV loading, cleaning, column classification
├── analytics/                # KPIs, trend, drivers, anomalies — 100% local
├── insight/                  # facts packet builder, prompts, budget, cache,
│                              # OpenRouter client (the only networked module)
├── ui/                       # Streamlit views + shared widgets
├── export/                   # local PPTX export (chart images + deck builder)
├── tests/                    # unit tests + messy fixture CSVs
├── Dockerfile
├── requirements.txt
└── .env.example
```

## Data privacy

- Uploaded CSVs live only in the session's memory/temp storage; raw row-level records are never persisted to a database or disk in v1.
- Derived aggregated narratives and token usage are cached locally on disk (`.aivara_insight_cache.json`, `.aivara_budget_state.json`) strictly keyed by cryptographic content hashes, while view-level exports remain scoped to in-memory session state.
- The OpenRouter API key is read from the environment only — never logged,
  never displayed in full in the UI.
- Only the small facts packet (a handful of aggregated numbers, typically
  100–300 tokens) ever leaves the machine, and only when generating an
  insight — the raw CSV never crosses the network.

## Known limitations / out of scope for v1

- No conversational "ask a question" interface (planned as Phase 2 — see the
  product guide, Section 14).
- No multi-user accounts, roles, or permissions.
- No persistent multi-file history, scheduled refresh, or data warehouse
  connectors.
- Forecast tab is a visible nav stub only — no model behind it yet.

## LLM budget notes

OpenRouter's free-tier limits rotate over time — re-verify current figures at
https://openrouter.ai/docs/guides/routing/model-variants/free before relying
on the defaults in `config.py`. The app enforces its own daily ceiling
(`AIVARA_DAILY_BUDGET`, default 30) independently of whatever OpenRouter's
current cap is, and always fails soft: if the budget is exhausted or the API
errors, the dashboard still renders fully with computed numbers — only the
narrative insight text is skipped.
