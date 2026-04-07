from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.ollama_adapter import OllamaError, check_ollama_health
from src.web_dispatch import CreditcardRuntime, dispatch_query, load_web_config


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "web_static"

config = load_web_config()
runtime = CreditcardRuntime(config)

app = FastAPI(title="Creditcard Web Chat", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class DispatchRequest(BaseModel):
    query: str = Field(min_length=1)
    intent: str | None = None
    context: dict[str, Any] | None = None
    doc_id: str | None = None
    dataset_id: str | None = None


def _inject_widget(html: str) -> str:
    meta_doc = '<meta name="report-doc-id" content="creditcard.xlsx">'
    meta_ds = '<meta name="report-dataset-id" content="Sheet_1">'
    script = '<script src="/static/creditcard_widget.js?v=1"></script>'

    out = html
    if "report-doc-id" not in out and "</head>" in out:
        out = out.replace("</head>", f"  {meta_doc}\n  {meta_ds}\n</head>")
    if "/static/creditcard_widget.js" not in out and "</body>" in out:
        out = out.replace("</body>", f"{script}\n</body>")
    elif "/static/creditcard_widget.js" not in out:
        out += "\n" + script
    return out


@app.get("/health")
def health() -> dict[str, Any]:
    db_ready = False
    db_error = None
    try:
        runtime.ensure_index_ready()
        db_ready = True
    except Exception as exc:  # pragma: no cover - health endpoint reports failures.
        db_error = str(exc)

    ollama = check_ollama_health(config.ollama)
    return {
        "ok": bool(db_ready),
        "db_ready": db_ready,
        "db_error": db_error,
        "db_path": config.db_path,
        "report_id": config.report_id,
        "ollama": ollama,
        "allow_deterministic_fallback": config.allow_deterministic_fallback,
    }


@app.get("/", response_class=HTMLResponse)
@app.get("/report", response_class=HTMLResponse)
def report_page() -> HTMLResponse:
    try:
        html = Path(config.html_path).read_text(encoding="utf-8", errors="ignore")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unable to load report HTML: {exc}") from exc
    return HTMLResponse(_inject_widget(html))


@app.post("/agent/dispatch")
def agent_dispatch(payload: DispatchRequest) -> dict[str, Any]:
    try:
        return dispatch_query(
            runtime,
            query=payload.query,
            doc_id=payload.doc_id,
            dataset_id=payload.dataset_id,
            context=payload.context,
        )
    except OllamaError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
