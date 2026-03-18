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

- No LLM summarization/paraphrasing (by design).
- No API server in this milestone.
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
