# infoapp-dynamic-retrieval

Notebook-first MVP for indexing report JSON/HTML files into SQLite (FTS5) and querying them via retrieval-only chat.

## Quick start

1. Install dependencies:
   - `pip install beautifulsoup4 lxml ipywidgets`
2. Open:
   - `notebooks/mvp_chat.ipynb`
3. Run all cells:
   - builds `data/reports_index.db`
   - opens a simple chat UI with current-report-first retrieval + cross-report fallback
