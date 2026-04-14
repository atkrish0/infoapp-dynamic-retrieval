# infoapp-dynamic-retrieval

Notebook-first MVP for indexing analytical report files (`.json` + `.html`) into a single SQLite/FTS5 search index, then answering questions with deterministic retrieval (no LLM).

The system is designed to be simple and grounded:
- prioritize the currently selected report,
- fallback to cross-report retrieval only when needed,
- return evidence snippets with source metadata.

## 1) Project goal

Build a minimum viable retrieval layer for report Q&A:
- ingest source files from `_reports_json/` and `_reports_html/`,
- index report metadata + schema + charts + rows,
- query with current-doc-first logic,
- expose a basic chat UI in Jupyter.

This is intentionally not production-hardening yet; it is a working prototype for fast iteration.

## 2) Repository layout

`_reports_json/`
- Source JSON report files.

`_reports_html/`
- Source HTML report files.

`src/indexer.py`
- Index builder and parsers for JSON/HTML.

`src/retriever.py`
- FTS search, scoring, sufficiency checks, fallback routing.

`src/chat.py`
- Chat turn wrapper that formats grounded answers from retrieval output.

`src/agent.py`
- Agentic orchestration layer (tool call flow + optional LLM synthesis).

`src/llm.py`
- Optional OpenAI Responses API client for grounded answer synthesis.

`src/creditcard_indexer.py`
- Dedicated indexer for credit-card HTML + Excel pair into SQLite.

`src/creditcard_query.py`
- SQL-first query engine for credit-card use case (aggregates, row lookup, FTS fallback).

`notebooks/mvp_chat.ipynb`
- Main interactive notebook UI.

`notebooks/creditcard_sql_chat.ipynb`
- Dedicated notebook for credit-card HTML + Excel use case.

`data/`
- Runtime output folder for SQLite index (`reports_index.db`).

## 3) Architecture overview

### Ingestion/indexing layer
- Reads all JSON + HTML reports.
- Writes normalized records to SQLite tables:
  - `documents`
  - `chunks`
  - `chunks_fts` (FTS5 virtual table)
- Uses triggers so `chunks_fts` stays synchronized with `chunks`.

### Retrieval layer
- Tokenizes query.
- Runs FTS against current document first.
- Applies lightweight scoring and sufficiency thresholds.
- If insufficient, expands search across all documents.
- Returns one of:
  - `current_doc`
  - `cross_doc`
  - `insufficient`

### Chat layer
- Calls `retrieve(...)`.
- Produces deterministic text response using top evidence.
- Includes evidence payload for rendering citations/snippets.

## 4) Data model

### `documents`
Columns:
- `doc_id` (PK): document identifier (filename-based)
- `source_path`: absolute/relative file path indexed
- `source_type`: `json` or `html`
- `title`: detected title
- `dataset_id`: best-effort dataset identifier
- `updated_at`: UTC timestamp of indexing

### `chunks`
Columns:
- `id` (PK)
- `doc_id` (FK to `documents`)
- `chunk_type` (examples: `meta`, `schema`, `chart`, `row`, `html_structure`)
- `section` (logical grouping)
- `content_text` (searchable text)
- `content_json` (raw structured payload as JSON string)
- `row_num` (for row chunks)
- `score_hint` (light prior weight)

### `chunks_fts`
- FTS5 index over `content_text`.
- External-content style sync via triggers on `chunks`.

## 5) What gets indexed

### JSON reports
- Project/document metadata.
- Dataset schema (columns).
- Chart specs from `infoElements.rootTags` (`type=chart`).
- Row-level data records.
- Summary chunks (rows/columns/charts counts).

### HTML reports
- Title and selected metadata (`report-doc-id`, `report-dataset-id`, `description`).
- Structural cues (e.g., widget/script signals) as lightweight searchable chunks.

### Field alias normalization
Row keys like:
- `$m$a`, `a`, `a$org`, `a$date`, `$m$a$org`, `$m$a$date`
are mapped toward human-readable column names where possible.

## 6) Public interfaces

`build_index(json_dir: str, html_dir: str, db_path: str) -> None`
- Rebuilds index database from source folders.

`retrieve(query: str, current_doc_id: str, db_path: str, k: int = 8) -> dict`
- Executes current-doc-first retrieval + fallback.
- Returns mode + evidence list.

`chat_turn(query: str, current_doc_id: str, db_path: str) -> dict`
- One retrieval-grounded response turn for UI/API use.

`agent_chat_turn(query: str, current_doc_id: str, db_path: str, use_llm: bool = True) -> dict`
- Agentic flow:
  - retrieve (tool step),
  - optional second-pass query expansion,
  - optional LLM synthesis (grounded to evidence only),
  - includes `trace`, `intent`, `llm_used`, `llm_error`.

Response shape:
- `answer: str`
- `mode: "current_doc" | "cross_doc" | "insufficient"`
- `evidence: list[dict]` with:
  - `doc_id`, `chunk_type`, `snippet`, `row_num`, `source_path`, `score`

## 7) Setup

### Requirements
- Python 3.10+
- `beautifulsoup4`
- `lxml`
- `ipywidgets` (for notebook UI)

Install:

```bash
pip install beautifulsoup4 lxml ipywidgets
```

## 8) Runbook (what to run + expected output)

### A) Build index from CLI

Run:

```bash
python - <<'PY'
from src.indexer import build_index
import sqlite3

build_index('_reports_json', '_reports_html', 'data/reports_index.db')
conn = sqlite3.connect('data/reports_index.db')
print('documents=', conn.execute('select count(*) from documents').fetchone()[0])
print('chunks=', conn.execute('select count(*) from chunks').fetchone()[0])
print('doc_ids=', [r[0] for r in conn.execute('select doc_id from documents order by doc_id')])
conn.close()
PY
```

Expected output pattern:
- `documents= 6`
- `chunks= <non-zero large number>` (current sample data ~`1285`)
- `doc_ids=` list containing:
  - `bar-only.html`
  - `bar-only.json`
  - `credit_report.html`
  - `credit_report.json`
  - `piechart.html`
  - `piechart.json`

### B) Run retrieval examples from CLI

Run:

```bash
python - <<'PY'
from src.retriever import retrieve
from src.indexer import build_index

build_index('_reports_json', '_reports_html', 'data/reports_index.db')

examples = [
    ('bar-only.json', 'charge in march 2024'),
    ('bar-only.json', 'Cube Eatery category'),
    ('bar-only.json', 'capital of mars'),
]
for doc, q in examples:
    r = retrieve(q, doc, 'data/reports_index.db')
    print('\\nquery=', q)
    print('mode=', r['mode'], 'evidence_count=', len(r['evidence']))
    if r['evidence']:
        print('top=', r['evidence'][0]['doc_id'], r['evidence'][0]['chunk_type'])
PY
```

Expected behavior:
- Query about local bar report data -> `mode=current_doc`
- Query needing other reports -> `mode=cross_doc`
- Out-of-domain query -> `mode=insufficient`

### C) Run notebook UI

Run:
1. Open `notebooks/mvp_chat.ipynb`.
2. Run all cells top to bottom.

Expected behavior:
- Cell output shows:
  - index file path (`data/reports_index.db`)
  - indexed document count
  - sample document IDs
- Interactive UI appears with:
  - report dropdown,
  - question textbox,
  - Send/Clear buttons + `Use LLM synthesis` toggle,
  - chat panel,
  - evidence accordion.

### D) Optional LLM synthesis (agent mode)

By default, the notebook works without LLM.  
To enable LLM synthesis, set env var before launching Jupyter:

```bash
export OPENAI_API_KEY="your_key_here"
export OPENAI_MODEL="gpt-4.1-mini"   # optional override
```

Then enable `Use LLM synthesis (if configured)` in the UI.

Behavior:
- If key is present and call succeeds: `llm_used=True` in agent output line.
- If key missing/fails: automatic deterministic fallback (no crash).

## 9) Current answer policy

- If current report is sufficient:
  - response starts with `Answer from current report:`
- If fallback was required and confident:
  - response starts with `Current report was insufficient; using cross-report context.`
- If not enough confidence:
  - response starts with `Not enough indexed evidence...`

## 10) Known limitations (MVP)

- Main report-index notebook path is still deterministic-first and heuristic-scored.
- Browser web app currently targets the credit-card use case only.
- Retrieval scoring is heuristic and intentionally simple.
- HTML deep semantic extraction is lightweight.
- No automated test suite committed yet (kept on standby as requested).

## 11) Troubleshooting

`ModuleNotFoundError` for widgets/bs4/lxml:
- Install required packages with `pip install beautifulsoup4 lxml ipywidgets`.

`no such module: fts5` (rare Python/SQLite build issue):
- Use a Python build with SQLite FTS5 enabled.

Notebook UI not rendering:
- Ensure notebook is running in Jupyter environment with widget support.

## 12) Next milestone ideas

- Add lightweight FastAPI endpoint (`/agent/dispatch`) using same `chat_turn` backend.
- Add saved regression test script for retrieval modes and alias handling.
- Add chunk-level citation anchors and richer table rendering in notebook.

## 13) Credit-card HTML + Excel flow (new)

This project now includes a dedicated path for the credit-card report pair:
- `_references/creditcard/creditcard.html`
- `_references/creditcard/Sample Ledger Credit Card Updated.xlsx`

Design:
- Excel is treated as canonical row-level source.
- HTML contributes report metadata/schema context.
- Both are loaded into SQLite (`data/creditcard_index.db`).
- Chat queries are answered via SQL first, then FTS fallback.

Run:

```bash
python - <<'PY'
from src.creditcard_indexer import build_creditcard_index
from src.creditcard_query import creditcard_chat_turn

build_creditcard_index(
    '_references/creditcard/Sample Ledger Credit Card Updated.xlsx',
    '_references/creditcard/creditcard.html',
    'data/creditcard_index.db',
    report_id='creditcard',
    rebuild=True,
)

for q in [
    'show row for Cube Eatery on 2022-01-01',
    'total charge for March 2022',
    'count transactions for Lunch tag',
]:
    r = creditcard_chat_turn(q, 'data/creditcard_index.db', report_id='creditcard', limit=5)
    print(q, '->', r['mode'], r['answer'])
PY
```

Expected output pattern:
- row query -> `mode=sql_rows`
- aggregate query -> `mode=sql_agg`
- unsupported/weak query -> `mode=fts_rows` or `mode=insufficient`

## 14) Browser HTML chat (FastAPI + SQL + Ollama)

This path reuses existing credit-card SQL indexing/query logic and adds a thin web layer.

New files:
- `app_web.py` (FastAPI entrypoint)
- `src/web_dispatch.py` (dispatch orchestration)
- `src/ollama_adapter.py` (Ollama synthesis + health checks)
- `web_static/creditcard_widget.js` (browser chat widget)

Install web dependencies:

```bash
pip install -r requirements-web.txt
```

### First-time setup (one time)

```bash
cd /Users/atheeshkrishnan/AK/DEV/infoapp-dynamic-retrieval
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements-web.txt
pip install ipywidgets
ollama pull llama3.1
```

### Normal run (subsequent times)

You do **not** need to recreate venv, reinstall packages, or re-pull the same model each run.

Terminal A (Ollama):

```bash
# only if Ollama is not already running
ollama serve
```

Terminal B (web app):

```bash
cd /Users/atheeshkrishnan/AK/DEV/infoapp-dynamic-retrieval
source .venv/bin/activate
uvicorn app_web:app --reload --port 8000
```

Run server:

```bash
uvicorn app_web:app --reload --port 8000
```

Open in browser:
- `http://localhost:8000/report`

Health check:
- `http://localhost:8000/health`

Optional env vars:

```bash
export CREDITCARD_HTML_PATH=\"_references/creditcard/creditcard.html\"
export CREDITCARD_EXCEL_PATH=\"_references/creditcard/Sample Ledger Credit Card Updated.xlsx\"
export CREDITCARD_DB_PATH=\"data/creditcard_index.db\"
export REPORT_ID=\"creditcard\"
export OLLAMA_BASE_URL=\"http://localhost:11434\"
export OLLAMA_MODEL=\"llama3.1\"
export OLLAMA_TIMEOUT_SEC=\"45\"
export ALLOW_DETERMINISTIC_FALLBACK=\"true\"
```

Behavior:
- `/agent/dispatch` always runs SQL retrieval first (`creditcard_chat_turn`).
- Then it attempts Ollama synthesis with evidence citations.
- If Ollama fails and fallback is enabled, deterministic SQL answer is returned with debug metadata.

Notes:
- If `ollama serve` says `address already in use`, Ollama is already running; continue.
- `ollama pull llama3.1` is needed only once per machine/model unless you remove the model.

Quick smoke checks (with server running):

```bash
./commands/web_smoke.sh
```

Or with a custom base URL:

```bash
./commands/web_smoke.sh http://localhost:8000
```

## 15) Web chatbot sample prompts

These are good prompts for the browser chat at `/report`:

1. `show row for Cube Eatery on 2022-01-01`
- Expected: row-level answer, usually `mode=sql_rows`, citation to a matching row.

2. `total charge for March 2022`
- Expected: aggregate answer, `mode=sql_agg`, with a total charge value.

3. `count transactions for Lunch tag`
- Expected: aggregate answer, `mode=sql_agg`, with transaction count.

4. `average payment in 2022`
- Expected: aggregate answer, `mode=sql_agg`, with average payment.

5. `what is vendor cube eatery category`
- Expected: row-level answer (`mode=sql_rows` or `fts_rows`) showing category values from matching rows.

6. `show transactions for Halcyon Hotel in November 2022`
- Expected: filtered row set via SQL/FTS path with row citations.

7. `show payments on 2022-01-04`
- Expected: row-level results for matching date, usually `mode=sql_rows`, including `payment` values when present.

8. `how many transactions in 2022`
- Expected: aggregate count, `mode=sql_agg`, with `txn_count=<number>`.

9. `total payment for 2022`
- Expected: aggregate metric, `mode=sql_agg`, with `total_payment=<value>`.

10. `show Business transactions in March 2022`
- Expected: filtered row results, `mode=sql_rows` (or `fts_rows` fallback), with citations for matching rows.
