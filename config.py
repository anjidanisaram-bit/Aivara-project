"""
config.py — central place for env/secrets loading, budget constants, and model choice.

Nothing in this file should be hardcoded secrets. The OpenRouter API key is
read from the environment only; it is never logged and never echoed back in
full anywhere in the UI (see ui/components.py for the masking helper).
"""

from __future__ import annotations

import os

try:
    from dotenv import load_dotenv  # python-dotenv

    load_dotenv()
except ImportError:
    # dotenv is a convenience for local dev; the app must still run without it
    # (e.g. inside Docker where env vars are injected directly).
    pass


# ---------------------------------------------------------------------------
# OpenRouter / LLM configuration
# ---------------------------------------------------------------------------

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.environ.get(
    "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1/chat/completions"
)

# Keep the model id here, not hardcoded inline in insight/client.py, since
# free-model availability on OpenRouter rotates over time.
DEFAULT_MODEL = os.environ.get("AIVARA_MODEL", "openrouter/auto")
FALLBACK_MODEL = os.environ.get("AIVARA_FALLBACK_MODEL", "meta-llama/llama-3.1-8b-instruct:free")

# Only used if the user explicitly flips the "allow paid models" toggle in the
# settings UI. Never used by default.
ALLOW_PAID_MODELS_DEFAULT = False

# ---------------------------------------------------------------------------
# Budget enforcement (see insight/budget.py)
# ---------------------------------------------------------------------------

# Comfortably under OpenRouter's free-tier daily cap (verify current limits at
# https://openrouter.ai/docs/guides/routing/model-variants/free — the account
# free-request daily cap has historically been 50/day with no top-up, 1000/day
# after a one-time >=$10 credit top-up; we default conservatively).
DAILY_CALL_BUDGET = int(os.environ.get("AIVARA_DAILY_BUDGET", "30"))

# Per-call cap on generated tokens, to bound both cost and rambling output.
MAX_OUTPUT_TOKENS = int(os.environ.get("AIVARA_MAX_OUTPUT_TOKENS", "150"))

# Where the daily call counter is persisted so it survives app restarts.
BUDGET_STATE_PATH = os.environ.get("AIVARA_BUDGET_STATE_PATH", ".aivara_budget_state.json")

# Where the facts-hash -> LLM response cache is persisted for the session.
INSIGHT_CACHE_PATH = os.environ.get("AIVARA_INSIGHT_CACHE_PATH", ".aivara_insight_cache.json")

# Prompt version string — bump this whenever prompts.py changes meaningfully,
# so cache keys don't silently reuse insights generated under an old prompt.
PROMPT_VERSION = "v1"

# ---------------------------------------------------------------------------
# Classification / analytics thresholds (Section 8.2-8.6)
# ---------------------------------------------------------------------------

DATE_PARSE_SUCCESS_THRESHOLD = 0.80  # >80% of non-null values must parse as dates
DIMENSION_UNIQUE_RATIO_CAP = 0.05  # <5% unique values (relative to row count)
DIMENSION_UNIQUE_ABS_CAP = 200  # ... or under this many distinct values, whichever is looser
ID_UNIQUE_RATIO_FLOOR = 0.95  # near-unique per row

ANOMALY_ZSCORE_THRESHOLD = 2.0
ANOMALY_PCT_SWING_THRESHOLD = 0.30  # 30% swing fallback when history is short

# Max rows to hold in memory before we sample for expensive operations.
MAX_ROWS_FULL_PROCESSING = 500_000

# ---------------------------------------------------------------------------
# App metadata
# ---------------------------------------------------------------------------

APP_TITLE = "Aivara Insight Lite"
APP_TABS = ["Executive", "Revenue", "Operations", "Customers", "Charts", "Risk", "Forecast"]
