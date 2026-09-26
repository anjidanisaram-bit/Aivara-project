# Aivara Insight Lite — Engineering Audit & Fix Brief

**Purpose of this document.** This is a code-level audit of the `aivara-insight-lite`
repository, written to hand to an AI coding agent (or a human engineer) as a work
order. Every item below was verified by reading the source and, where practical,
reproduced with a short script (repro snippets are included so you can confirm the
bug still exists before and after your fix). Line numbers refer to the code as of
this audit; re-check them if the file has since changed.

Work through **Section 1 (Critical Bugs)** first — these produce wrong numbers or
wrong data shown to the wrong person, silently. Then **Section 2 (Dead Ends)** —
features that are partially built and never wired up, which confuse anyone reading
the codebase. Then **Section 3 (Silent Failures)** — places that swallow errors or
drop data with no user-facing signal. Section 4 covers design gaps worth a deliberate
decision rather than a quick patch. Section 5 is test coverage. Section 6 is a
suggested fix order.

Nothing here requires re-architecting the app — every fix is local to one or two
files.

---

## 1. Critical bugs (wrong output, silently)

### 1.1 Cross-user insight cache — one user's AI insight can be shown to another user

**Files:** `insight/cache.py`, `ui/components.py` (`render_insight_card`),
`export/pptx_builder.py` (`_cached_insight_for_view`)

`insight_cache.set_for_view(view_name, ...)` / `get_for_view(view_name, ...)` key
the cache purely by the generic view name — `"view::Executive"`, `"view::Revenue"` —
in a **single shared JSON file on disk** (`.aivara_insight_cache.json`), not scoped
by session, user, or dataset. The README explicitly frames this as a tool "a small
internal team" will share. If two people use the deployed instance around the same
time (or even just sequentially without a process restart in between, depending on
how the app is hosted), whichever insight was written last to `"view::Executive"`
is what `_cached_insight_for_view("Executive")` returns for **anyone** viewing the
Executive tab or exporting a PPTX — potentially someone else's company's numbers
narrated on top of your KPI tiles, with zero indication anything is wrong.

The facts-hash-keyed cache in the same file (used by `generate_insight` for the
on-screen insight card) is content-addressed and doesn't have this problem — only
the `view::*` keys used for PPTX export are affected. But that's exactly the
artifact people download and hand to their manager.

**Fix direction:** scope the `view::*` cache key by a per-session identifier (e.g.
`st.session_state` already has a natural session lifetime — generate a UUID into
session state on first load and include it in the key) or, more simply, stop
persisting the `view::*` lookup to the shared file at all and keep it in
`st.session_state` only (it's a same-run, same-user convenience lookup for export;
it does not need to survive a process restart the way the budget counter does).

### 1.2 Trend chart plots the wrong metric's numbers under the right metric's name

**Files:** `app.py` (`run_pipeline`, lines ~54–59), `ui/executive_view.py`,
`ui/revenue_view.py`

```python
if date_col and metric_cols:
    primary_for_trend = metric_cols[0]          # <- first column in CSV/classifier order
    trend = build_trend_series(df, date_col, primary_for_trend)
...
kpis = compute_all_kpis(df_current, df_prior, metric_cols)
primary_metric = next(iter(kpis), None)          # <- metric with the largest |sum|
```

`primary_for_trend` is whichever metric column happens to appear first; `primary_metric`
(used everywhere else, including as the chart *title*) is the metric with the
largest absolute total. These are frequently **different columns**. Both
`executive_view.py` and `revenue_view.py` then do:

```python
fig = build_trend_figure(trend.series, title=primary_name.replace("_", " ").title())
```

— i.e. they title the chart after `primary_metric` but plot `trend.series`, whose
values were resampled from `primary_for_trend` (`metric_cols[0]`). If your CSV has
columns in the order `units, revenue, cost` and revenue has the largest total, the
chart is titled "Revenue" but plots `units`. This also gets baked into the PPTX
export unchanged, since the export figures are the exact same `go.Figure` objects.

**Repro:** any dataset where the first metric column by CSV order isn't the metric
with the largest absolute sum (the `clean.csv` test fixture — columns
`revenue, cost, units` — happens to dodge this because `revenue` is both first and
largest; reorder the columns or add a bigger metric first and it reproduces
immediately).

**Fix direction:** compute `kpis`/`primary_metric` first, then build the trend from
`primary_metric`, not `metric_cols[0]`. `run_pipeline()` currently does these in the
wrong order for this reason — swap them.

### 1.3 Driver contribution % is meaningless (`0.0%`) whenever gains and losses offset

**File:** `analytics/drivers.py`, `compute_dimension_drivers` (line ~74)

```python
contribution_pct = (delta / total_delta * 100) if total_delta != 0 else 0.0
```

When the net change across categories is exactly zero — e.g. North +1000,
South −1000 — `total_delta == 0`, so **every** category is reported as contributing
`0.0%`, even though North and South individually moved by a very large amount. This
is one of the most common real-world driver-analysis scenarios (share shifting
between segments with a flat total) and it's exactly the case this feature exists
to explain — but it silently reports "nothing happened" for every category.

Verified:
```
total_delta: 0.0
South delta = -1000.0  contribution_pct = 0.0
North delta = +1000.0  contribution_pct = 0.0
```

There's a second, sharper version of the same bug: when `total_delta` is small but
*not quite* zero (e.g. 0.01) while individual category deltas are large, the
division blows up into meaningless numbers like `+5,000,000%` with no floor/guard —
this will show up verbatim in the UI, the facts packet sent to the LLM, and the
PPTX.

**Fix direction:** contribution should be normalized against something more stable
than the net delta — e.g. the sum of absolute category deltas (`Σ|delta_i|`) rather
than the signed total, so offsetting moves both show up as large contributions in
their respective direction. Add a floor on the denominator regardless of which
normalization you pick.

### 1.4 `pct_change` is always `+inf`, even for a negative swing from zero

**File:** `analytics/kpis.py`, `compute_kpi` (line ~74)

```python
elif current_total != 0:
    pct_change = float("inf")
```

This branch fires whenever `prior_total == 0` and `current_total != 0` — it doesn't
check the sign of `current_total`. A metric that went from `0` to `-500` is reported
as `+inf` (i.e. "infinite growth"), which is backwards. Verified:

```
current=-500, prior=0 -> pct_change = inf
```

**Fix direction:** `pct_change = float("inf") if current_total > 0 else float("-inf")
if current_total < 0 else 0.0`. Then also fix every place that *displays*
`pct_change` (next item) to handle both signs of infinity, not just skip it.

### 1.5 `+inf%` / `inf%` renders literally in the UI and the exported PPTX

**Files:** `ui/components.py` (`render_kpi_tile`), `app.py`
(`render_export_button`, the `exec_tiles` comprehension)

Three places consume `kpi.pct_change`. Only one of them guards against infinity:

- `insight/facts_builder.py` — **does** guard: `if kpi.pct_change not in (None,
  float("inf"))` (though see 1.4 — this needs to also exclude `-inf` once that's fixed).
- `ui/components.py::render_kpi_tile` — **does not guard**:
  `f"{abs(kpi.pct_change):.1f}%"` → literally renders `"inf%"` in the Streamlit KPI
  tile.
- `app.py::render_export_button` — **does not guard**:
  `f"{k.pct_change:+.1f}%"` → literally renders `"+inf%"` baked into the exported
  PPTX deck's KPI tile.

**Fix direction:** add one small helper (e.g. `format_pct_change(x)` in
`ui/components.py`) that both call sites use, and have it render something like
"new" or "n/a (no prior period)" for infinite/undefined changes. Don't fix this
independently in three places — that's how it drifted out of sync the first time.

### 1.6 Schema confirmation gate doesn't reset when a new file is uploaded

**File:** `ui/upload_page.py`, `render_upload_page`

When a new (differently-named) file is uploaded, the function correctly resets
`dataset`, `classifications`, `_uploaded_filename`, `dataset_name`, and
`overrides` — but it never resets `st.session_state["schema_confirmed"]`. Since the
function's return value is `bool(st.session_state.get("schema_confirmed"))`, once a
user has confirmed a schema once in a session, **every subsequent upload skips the
confirmation gate**: `app.py::main()` will proceed straight to building the
dashboard using the fresh Tier-1 auto-classification for the new file, while the
schema review table is still shown on the same page, unconfirmed, with the
"Confirm schema and build dashboard" button now doing nothing meaningful (the
dashboard is already rendering below it). If the auto-classifier got something
wrong for the second file, the user has no forcing function to catch it before
seeing (and exporting) a dashboard built on a wrong schema.

**Fix direction:** reset `schema_confirmed = False` in the same block that resets
the other per-file session-state keys.

### 1.7 Re-uploading a same-named file with different content is silently ignored

**File:** `ui/upload_page.py`, line ~40

```python
if st.session_state.get("_uploaded_filename") != uploaded.name:
```

Re-processing is gated purely on filename. If a user fixes something in their
source data and re-exports to the same filename (extremely common — "export.csv",
"report.csv", scheduled exports that overwrite the same name each time), the app
will keep showing the **old** cached dataset and never re-read the new upload.

**Fix direction:** hash the uploaded bytes (e.g. `hashlib.md5(file_bytes).hexdigest()`)
and key the "is this a new upload" check off content, not filename — or at minimum
show an explicit "Re-process this file" button so the user isn't silently stuck on
stale data.

### 1.8 Stale export figures leak across datasets

**Files:** `ui/executive_view.py`, `ui/revenue_view.py`, `app.py`
(`render_export_button`)

`st.session_state.setdefault("_export_figures", {})["Executive_trend"] = fig` is
only set on the branch where `trend is not None`. The `else` branch (no usable date
column) does not clear the key. Session state for `_export_figures` and
`_pptx_buffer` is also never reset on new upload (see 1.6/1.7's reset block — it's
missing here too).

Concretely: upload dataset A (has a date column, trend chart renders, cached under
`_export_figures["Executive_trend"]`) → click "Prepare PPTX export" → upload
dataset B (no usable date column) → click "Prepare PPTX export" again → the deck
for dataset B will silently include dataset A's trend chart, because the key was
never cleared and nothing in dataset B's run overwrote it.

**Fix direction:** clear `_export_figures` and `_pptx_buffer` from session state in
the same reset block identified in 1.6/1.7, whenever a new file is loaded.

---

## 2. Dead ends (built, never connected, or connected to nothing)

These aren't bugs in the sense of producing wrong output — they're half-finished
wiring. Anyone reading the code (including a future AI agent) will reasonably
assume these features work because the code exists, is exported cleanly, and even
has "used by X" docstrings pointing at call sites that don't actually exist.

### 2.1 Tier-2 LLM column classification is fully built and never called

The product guide (Section 8.2) and the module docstring in
`ingestion/classifier.py` describe a two-tier system: fast rule-based
classification, then an LLM fallback for ambiguous columns. Tier 2 is completely
implemented:

- `ingestion/classifier.py::build_llm_classification_payload` — builds the payload.
- `insight/client.py::classify_ambiguous_columns` — calls OpenRouter, parses the
  response, caches it.
- `insight/prompts.py::COLUMN_CLASSIFICATION_SYSTEM_PROMPT` /
  `build_column_classification_prompt` — the prompt.

None of these three are ever imported or called from `app.py` or
`ui/upload_page.py`. The only thing `ui/upload_page.py` actually does with a
low-confidence column is show a "⚠️ low confidence" label next to the manual
override dropdown — the user has to notice and fix it themselves. This is a
reasonable v1 fallback, but it means the "LLM fallback for ambiguous columns"
feature described in the product guide doesn't run in the shipped app at all.

**Fix direction:** either wire it in (call `classify_ambiguous_columns` for
columns below the confidence threshold when the user confirms the schema, treating
a `BudgetExhaustedError` as "keep the Tier-1 guess" per the existing docstring), or
remove the dead code and update the product guide/README so nobody expects it to
run.

### 2.2 "Allow paid models" is documented but has no UI control

`config.ALLOW_PAID_MODELS_DEFAULT` has a comment: *"Only used if the user
explicitly flips the 'allow paid models' toggle in the settings UI."* No such
toggle exists anywhere in `app.py::render_sidebar`. The model text input lets a
user type any model string, including a paid one — but `_call_openrouter` will
silently substitute `FALLBACK_MODEL` for it with no message shown to the user, so
someone who typed a specific paid model to test it will get a different, free
model back with no indication of the substitution.

**Fix direction:** either add the toggle the comment promises, or — simpler for
v1 — surface the substitution: if `_call_openrouter` swaps the model, return which
model was actually used (it already does, via `InsightResult.model`) and have
`render_insight_card` show it when it differs from what was requested.

### 2.3 `MAX_ROWS_FULL_PROCESSING` — no sampling/guard exists anywhere

`config.py` defines `MAX_ROWS_FULL_PROCESSING = 500_000` with a comment ("Max rows
to hold in memory before we sample for expensive operations"). It is never
referenced anywhere else in the codebase. There is no row-count check, no
sampling, no warning for large files anywhere in `ingestion/` or `analytics/`. A
very large CSV will be loaded, cleaned, classified, and have `groupby` /
`resample` operations run on it in full, with no guard.

**Fix direction:** either implement the guard this constant implies (e.g. sample
down for classification/anomaly detection above the threshold, with a note shown
to the user), or remove the constant so it stops implying a safety net that isn't
there.

### 2.4 `GRANULARITY_ORDER`, `estimate_tokens`, `SEVERITY_LOW` — unused

- `analytics/trend.py::GRANULARITY_ORDER = ["D", "W", "M"]` — never read anywhere.
- `insight/budget.py::estimate_tokens()` — fully implemented, unit-tested, and
  never called from any code path that actually sends a request. Nothing checks
  facts-packet size before sending it, despite the budget module's whole reason for
  existing being to control LLM usage cost/size.
- `analytics/anomalies.py::SEVERITY_LOW = "low"` — defined, and
  `ui/risk_view.py::SEVERITY_ICON` even has a `"low": "🟡"` entry waiting for it —
  but nothing in `anomalies.py` ever assigns `"low"` to a `RiskSignal`. Only
  `"high"` and `"medium"` are ever produced (both severity functions use
  `threshold` vs. `threshold * 1.5`/`* 2` with nothing below the base threshold
  counting as "low"). The `RiskSignal` schema quietly implies a three-tier severity
  system that only has two tiers in practice.

**Fix direction:** either use these (have `estimate_tokens` actually gate/warn on
oversized facts packets; add a genuine "low" severity band below the current
medium threshold) or delete them so the code doesn't advertise capabilities it
doesn't have.

### 2.5 "Customers" tab is a copy-paste of "Operations," not a customer-specific view

**File:** `app.py`, `main()`:

```python
with tabs[3]:
    st.header("Customers")
    st.caption("Customer-shaped dimension breakdowns appear here when a customer-like dimension is detected.")
    render_drivers_view(pipeline_result)
```

The caption promises customer-specific filtering ("when a customer-like dimension
is detected"), but `render_drivers_view` has no concept of "customer-like" — it's
the exact same function called in the Operations tab, rendering the exact same
`all_dimension_drivers` list. The Customers tab is currently indistinguishable from
Operations except for the header text.

**Fix direction:** either add a real customer-dimension heuristic (e.g. column name
matches `customer|client|account|user`) and filter `all_dimension_drivers` to it,
or collapse the two tabs until that's built, so the caption doesn't promise
something that isn't happening.

---

## 3. Silent failures (errors swallowed or data dropped with no signal)

### 3.1 `on_bad_lines="skip"` drops rows with zero user-facing notice

**File:** `ingestion/loader.py`, `load_csv` (lines ~217–232)

The 2nd and 3rd parse attempts pass `on_bad_lines="skip"`. If the first (strict)
attempt fails and one of these succeeds, malformed rows are silently dropped —
`notes` never gets an entry saying how many rows were skipped or why. This
directly contradicts the project's own stated principle (see
`ingestion/cleaner.py`'s docstring: *"Cleaning never silently drops or alters rows"*)
— the drop just happens one layer earlier, in the loader, where it isn't logged
at all.

**Fix direction:** pandas' `on_bad_lines` also accepts a callable
(`on_bad_lines=lambda bad_line: ...`) in recent versions — use it to count skipped
lines and add a note like `"N row(s) skipped: malformed (wrong column count)."` to
`LoadResult.notes` so it surfaces in the UI the same way every other ingestion
decision does.

### 3.2 Percent columns are silently mis-scaled by 100x

**File:** `ingestion/cleaner.py`, `_try_numeric_coerce` → `clean_value` (line ~45)

```python
is_percent = bool(_PERCENT_CHAR.search(v))
v = _PERCENT_CHAR.sub("", v)
...
# `is_percent` is never used again
```

The function detects a trailing `%` and strips it, but never divides by 100 (or
otherwise records that the column was a percentage). A column of `"45%", "50%",
"12%"` becomes the plain numbers `45, 50, 12` — silently. Verified:

```
percent coercion result: [45, 50, 12] (did convert: True)
```

This is worse than it looks because of how `analytics/kpis.py` treats every metric
column identically: it always aggregates by `.sum()`. A genuine rate/percentage
column that survives this bug as raw numbers will then get **summed across rows**
in the KPI tile (e.g. a "conversion_rate" column reported as "347" for the week,
the sum of seven daily percentages) — a second, compounding problem covered in 4.1
below.

**Fix direction:** when `is_percent` is true, either divide the parsed value by
100 and tag the column's unit as `%`, or leave percent-looking columns as text for
the classifier to route to a rate-aware aggregation once one exists (see 4.1) —
but don't silently drop the fact that it was a percentage.

### 3.3 Null-placeholder tokens are recognized inconsistently

**File:** `ingestion/cleaner.py`, line ~82

```python
df[col] = df[col].replace({"": np.nan, "nan": np.nan, "None": np.nan, "NULL": np.nan})
```

This only catches four exact-case strings. Common null placeholders that will
**not** be normalized: `"NaN"`, `"NAN"`, `"N/A"`, `"n/a"`, `"na"`, `"-"`, `"null"`
(lowercase), `"None"` already covered but `"none"` lowercase is not. Any of these
will survive as a literal text value — either polluting a dimension column with a
fake category (`"N/A"` showing up as a legitimate segment in driver analysis) or
silently failing numeric/date coercion for a column that's otherwise clean,
dragging its success-fraction below the 80%/90% threshold and leaving the whole
column as unconverted text.

**Fix direction:** case-insensitive match against a broader canonical set:
`{"", "na", "n/a", "nan", "none", "null", "-", "--"}`.

### 3.4 Partial coercion failures aren't counted or reported

**File:** `ingestion/cleaner.py`, numeric coercion (line ~59) and datetime
coercion (line ~137)

Both coercions use a threshold (90% for numeric, 80% for datetime): if *most* of a
column's values parse, the *whole* column is converted, and whatever didn't parse
becomes `NaN`/`NaT`. Neither path counts or reports how many values were lost this
way — `CleanReport.messages` only ever says "Coerced to numeric: X" / "Parsed as
dates: X", never "and N values in that column couldn't be parsed and are now
empty." A column that's 89% clean numbers and 11% stray text will silently lose
that 11% with no way for the user to know without inspecting the raw data
themselves.

**Fix direction:** compute and include the failed-value count in the message,
e.g. `"Coerced to numeric: revenue (3 value(s) could not be parsed and are now empty)."`

### 3.5 Numeric coercion runs before datetime coercion, misclassifying compact date formats

**File:** `ingestion/cleaner.py`, steps 5 and 6 (lines ~111–144)

A column of `YYYYMMDD`-style dates with no separators (e.g. `"20240101"`) is a
valid float per Python's `float()` builtin's sibling `pd.to_numeric` — so it will
be caught and permanently converted by the numeric-coercion pass (step 5), which
runs first. By the time the datetime-coercion pass (step 6) runs, the column is
already numeric dtype and is skipped (`_stringy_columns` no longer includes it).
Interestingly, `ingestion/loader.py::_DATE_SHAPE_PATTERN` already has an explicit
regex arm for exactly this format (`^\s*\d{4}\d{2}\d{2}\s*$`) for header detection
— the awareness exists in one module and not the other.

**Fix direction:** try datetime coercion before numeric coercion, or check the
date-shape pattern before accepting a numeric coercion, so compact-format date
columns aren't silently claimed by the wrong pass.

### 3.6 Missing/NaN dimension values are silently dropped from driver & anomaly analysis

**Files:** `analytics/drivers.py`, `analytics/anomalies.py` — both use
`df.groupby(dimension_col)` with pandas' default `dropna=True`.

Any row whose dimension value is null is excluded from every category's numbers in
driver decomposition and anomaly detection, with no count or note anywhere. If
15% of rows have a missing `region`, the driver breakdown quietly explains the
change using only the other 85%, presented with no caveat.

**Fix direction:** either group with `dropna=False` and surface a "(missing)"
bucket explicitly, or count and report the excluded row count in
`DimensionDriverResult`/the risk-signal output.

### 3.7 A single broken chart can abort the entire PPTX export instead of degrading gracefully

**Files:** `export/chart_images.py::figure_to_png_bytes`,
`export/pptx_builder.py::build_view_slide` / `build_drivers_slide`

`figure_to_png_bytes` only catches `ValueError` and re-raises as `RuntimeError`;
`build_view_slide`/`build_drivers_slide` only catch `RuntimeError` and skip the
picture. In practice, kaleido failures — especially in a minimal Docker image
missing a Chrome shared library (see 4.4) — commonly surface as other exception
types (`OSError` from the subprocess layer, kaleido-internal exceptions, or a hang
with no exception at all in some known kaleido 0.2.1 environments). Any of those
will propagate past both narrow `except` clauses and be caught only by
`app.py::render_export_button`'s top-level `except Exception`, which aborts the
**entire** PPTX build and shows a generic error — instead of the intended
per-chart graceful degradation ("fail soft — omit the picture rather than crash
export," per the code's own comment).

**Fix direction:** widen both catch clauses to `Exception` (this module's whole
job is "don't let a chart failure take down the export"), and consider wrapping
the kaleido call with a timeout given kaleido 0.2.1's known hangs on some Linux
setups.

### 3.8 LLM output parsing assumes exactly 3 clean sentences; real free-tier models won't reliably comply

**File:** `insight/client.py::_split_into_sentences` (line ~47) and
`generate_insight` (line ~104)

```python
parts = [p.strip() for p in text.replace("\n", " ").split(".") if p.strip()]
```

This splits on every literal `.`, including decimal points inside numbers. Given
the domain (financial figures, percentages), the model's response will routinely
contain numbers like `$30,000.50` or `81.8%` — each one fractures the split.
Verified:

```
0 'Revenue rose to $30,000'
1 '50 this week'
2 'North led with 81'
3 '8% of the gain'
4 "Investigate South's 9"
5 '1% decline before next review'
```

`generate_insight` then blindly takes `sentences[0]`, `[1]`, `[2]` as
headline/driver/action — in the example above, the "headline" would be "Revenue
rose to $30,000" (truncated) and the real content ends up split across driver/
action incoherently. This compounds with free-tier models, which are also not
reliable at following a rigid "exactly 3 sentences" instruction to begin with —
if the model returns 2, 5, or 1 sentence, the split degrades further with no
detection or retry.

Worse: **whatever comes out of this — even garbled — gets cached** (`cache.set`)
keyed by the facts packet's content hash, so a single bad parse for a given
dataset state is replayed identically forever until the underlying numbers change.
There's no quality gate before caching (e.g. rejecting empty or suspiciously short
headlines).

**Fix direction:** ask the model for a structured response (e.g. request JSON with
`headline`/`driver`/`action` fields explicitly, matching the pattern already used
successfully in `classify_ambiguous_columns`) instead of parsing free text by
sentence-splitting. At minimum, don't cache a result whose headline is empty or
whose sentence count doesn't match what was expected.

---

## 4. Design gaps worth a deliberate decision (not one-line fixes)

### 4.1 Every metric is aggregated with `.sum()` — wrong for rates, ratios, and averages

**Files:** `analytics/kpis.py`, `analytics/trend.py`

`compute_kpi`, `build_trend_series`, `compute_dimension_drivers`, and
`detect_group_outliers`/`detect_period_swings` all aggregate via `.sum()`
unconditionally. This is correct for additive metrics (revenue, units, cost) and
silently wrong for anything else the classifier calls a "metric" — conversion
rates, average order value, NPS scores, percentages (see 3.2), ratios. A
"conversion_rate" column summed across a week of daily rows produces a number with
no real-world meaning, presented in a KPI tile with just as much confidence as a
correct revenue total.

There's no concept anywhere in the classifier or KPI layer of "this metric should
be averaged/weighted, not summed." This is worth a real design decision (e.g. a
naming heuristic similar to `COST_NAME_PATTERN`/`REVENUE_NAME_PATTERN` — match
`rate|pct|percent|ratio|average|avg|score` and aggregate those with `.mean()`
instead of `.sum()`), not a quick patch, since it affects KPI computation, trend
resampling, and driver decomposition consistently.

### 4.2 "Anomaly" detection is a single-snapshot comparison across categories, not real time-series anomaly detection

**File:** `analytics/anomalies.py::detect_group_outliers`

This computes a z-score of each category's total **within one period**, relative
to the other categories in that same period. It will reliably flag a naturally
large category (e.g. an "Enterprise" tier that's always bigger than "SMB" by
design) as a statistical outlier every single time it runs, which isn't an anomaly
— it's just how the business is shaped. This isn't a bug in the sense of an
incorrect implementation of what's described, but it's worth flagging clearly to
whoever builds on this: the Risk tab's signal-to-noise ratio in practice will
likely be dominated by structural-size "outliers" rather than genuine anomalies,
for any dataset with naturally skewed category sizes (which is most real business
data — regions, tiers, products, etc.).

A more useful version would compare each category's *current-vs-its-own-history*
distribution rather than category-vs-category-in-one-period.

### 4.3 Cache and budget state files are not safe under multiple OS processes

**Files:** `insight/cache.py`, `insight/budget.py`

Both modules use `threading.Lock()` to guard a read-modify-write cycle over a JSON
file. This is safe within a single Python process with multiple threads, but:

- Streamlit can be run with multiple worker processes behind a reverse proxy, and
  a `threading.Lock` is **not shared across processes** — each process gets its
  own independent lock, so the read-modify-write is not atomic across processes.
- `budget.can_make_call()` and `budget.record_call()` are two separate,
  independently-locked calls (not one atomic check-and-increment) — even
  single-process, two nearly-simultaneous Streamlit sessions could both pass the
  budget check before either records its call, letting the shared daily budget be
  exceeded by however many requests raced each other.

Given the stated multi-user deployment model, this is worth a real fix (a file
lock via `fcntl`/`portalocker`, or moving to sqlite with a transaction) rather than
the current in-process-only locking, if this is ever deployed with more than one
worker process.

### 4.4 Docker image's kaleido dependency list is a known-incomplete subset

**File:** `Dockerfile`

The `apt-get install` list covers some but not all of the shared libraries
Chromium (which kaleido drives headlessly) typically needs on a `slim` base image.
Commonly missing from this list in similar setups: `libatk-bridge2.0-0`,
`libgbm1`, `libasound2`, `libcups2`, `libdrm2`, `libxdamage1`, `libxfixes3`,
`libxshmfence1`. Combined with 3.7's narrow exception handling, a missing library
here is likely to surface as an unhandled exception type inside the Docker
deployment specifically — i.e. PPTX export could work fine when developing locally
on a full desktop OS and fail (or hang — kaleido 0.2.1 has known hang issues on
some Linux configurations with an incomplete Chrome environment) only once
deployed via this Dockerfile. Worth testing the actual export path inside a
freshly built container before considering the Docker path production-ready.

### 4.5 README overstates the privacy story slightly

**File:** `README.md`, "Data privacy" section

> "Uploaded CSVs and all derived data live only in the session's memory/temp
> storage; nothing is persisted to a database in v1."

Technically true (no database), but `.aivara_insight_cache.json` and
`.aivara_budget_state.json` do persist real, aggregated business figures (KPI
values, driver percentages, risk descriptions) to local disk files that survive
process restarts and — per 1.1 — are not scoped per user. Worth tightening this
section once 1.1 is fixed, so the documentation matches what actually happens to
data after a session ends.

---

## 5. Test coverage gaps

Current test files: `test_loader.py`, `test_classifier.py`, `test_kpis.py`,
`test_drivers.py`, `test_budget.py`, `test_export_no_network.py`.

**Zero test coverage** for:

- `ingestion/cleaner.py` — no `test_cleaner.py` at all. This is the file with the
  most bugs found in this audit (3.2, 3.3, 3.4, 3.5) — a dedicated test file
  would have caught all four.
- `analytics/trend.py` — no `test_trend.py`. `pick_granularity`'s
  density-vs-span logic and `split_current_prior`'s boundary math are non-trivial
  and untested.
- `analytics/anomalies.py` — no `test_anomalies.py`. Zero tests for z-score
  outlier detection or period-swing detection.
- `insight/cache.py`, `insight/client.py`, `insight/facts_builder.py`,
  `insight/prompts.py` — no direct tests (the budget test file tests
  `insight/budget.py` only). `_split_into_sentences` (3.8) and the percent/`inf`
  handling in `facts_builder.py` are exactly the kind of thing a couple of unit
  tests would have caught before this audit.
- `export/chart_images.py` — no tests.

Suggested new fixture: a CSV with an offsetting-category scenario (to pin down
1.3's fix), a percent column (`"45%"` etc., to pin down 3.2), and a column of
`YYYYMMDD`-format dates (to pin down 3.5) would together cover the highest-value
gaps cheaply.

---

## 6. Suggested fix order

1. **1.1 (cross-user cache)** — highest severity; this is a real data-leak risk
   in the stated deployment model, and the fix is small (change what the cache
   key is built from).
2. **1.2 (mislabeled trend chart)** — silently wrong numbers under a confident
   label; also a small, well-scoped fix (reorder two blocks in `run_pipeline`).
3. **1.6 / 1.7 / 1.8 (session-state reset gaps)** — all three fixes live in the
   same few lines of `ui/upload_page.py`; do them together.
4. **1.3 / 1.4 / 1.5 (kpi/driver math + display)** — group these since 1.5's
   fix (a shared formatting helper) depends on 1.4's fix (correct sign) being
   done first.
5. **3.1 – 3.6 (ingestion/cleaning silent failures)** — write `test_cleaner.py`
   first (Section 5), then fix each item against a red test.
6. **2.1 – 2.5 (dead ends)** — lower urgency; each is a product decision (wire it
   up vs. delete it) more than a bug fix. Flag these to whoever owns the product
   guide before picking a direction.
7. **4.1 – 4.5 (design gaps)** — schedule as deliberate follow-up work, not
   quick patches; 4.1 in particular touches several files and needs a
   consistent convention, not a one-off fix.

---

## Appendix: file-by-file index of findings

| File | Findings |
|---|---|
| `app.py` | 1.2, 1.5, 1.8, 2.2, 2.5 |
| `config.py` | 2.2, 2.3 |
| `ingestion/loader.py` | 3.1, 3.5 (interaction) |
| `ingestion/cleaner.py` | 3.2, 3.3, 3.4, 3.5 |
| `ingestion/classifier.py` | 2.1 |
| `analytics/kpis.py` | 1.4, 1.5, 4.1 |
| `analytics/trend.py` | 1.2, 2.4, 4.1 |
| `analytics/drivers.py` | 1.3, 3.6 |
| `analytics/anomalies.py` | 2.4, 3.6, 4.2 |
| `insight/client.py` | 2.1, 2.2, 3.8 |
| `insight/cache.py` | 1.1, 4.3 |
| `insight/budget.py` | 2.4, 4.3 |
| `insight/facts_builder.py` | 1.4 (downstream), 3.2 (downstream) |
| `ui/upload_page.py` | 1.6, 1.7 |
| `ui/executive_view.py` | 1.2, 1.8 |
| `ui/revenue_view.py` | 1.2, 1.8 |
| `ui/components.py` | 1.5 |
| `export/chart_images.py` | 3.7 |
| `export/pptx_builder.py` | 1.1, 1.5, 3.7 |
| `Dockerfile` | 4.4 |
| `README.md` | 4.5 |
