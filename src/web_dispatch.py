from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .creditcard_indexer import build_creditcard_index
from .creditcard_query import creditcard_chat_turn
from .ollama_adapter import OllamaConfig, OllamaError, synthesize_with_ollama


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass
class WebDispatchConfig:
    report_id: str
    excel_path: str
    html_path: str
    db_path: str
    allow_deterministic_fallback: bool
    ollama: OllamaConfig


def load_web_config() -> WebDispatchConfig:
    return WebDispatchConfig(
        report_id=os.getenv("REPORT_ID", "creditcard"),
        excel_path=os.getenv(
            "CREDITCARD_EXCEL_PATH",
            "_references/creditcard/Sample Ledger Credit Card Updated.xlsx",
        ),
        html_path=os.getenv(
            "CREDITCARD_HTML_PATH",
            "_references/creditcard/creditcard.html",
        ),
        db_path=os.getenv("CREDITCARD_DB_PATH", "data/creditcard_index.db"),
        allow_deterministic_fallback=_env_bool("ALLOW_DETERMINISTIC_FALLBACK", True),
        ollama=OllamaConfig(
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            model=os.getenv("OLLAMA_MODEL", "llama3.1"),
            timeout_sec=int(os.getenv("OLLAMA_TIMEOUT_SEC", "45")),
        ),
    )


class CreditcardRuntime:
    def __init__(self, config: WebDispatchConfig):
        self.config = config
        self._lock = threading.Lock()
        self._ready = False

    def ensure_index_ready(self) -> None:
        with self._lock:
            if self._ready and Path(self.config.db_path).exists():
                return
            build_creditcard_index(
                self.config.excel_path,
                self.config.html_path,
                self.config.db_path,
                report_id=self.config.report_id,
                rebuild=False,
            )
            self._ready = True


def _citations_from_evidence(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    citations = []
    for idx, item in enumerate(evidence[:8], start=1):
        row_num = item.get("row_num")
        label = (
            f"{idx}. row {row_num} ({item.get('doc_id', 'source')})"
            if row_num is not None
            else f"{idx}. {item.get('doc_id', 'source')}"
        )
        citations.append(
            {
                "anchorId": f"row-{row_num}" if row_num is not None else f"src-{idx}",
                "label": label,
                "row_num": row_num,
                "doc_id": item.get("doc_id"),
            }
        )
    return citations


def dispatch_query(
    runtime: CreditcardRuntime,
    *,
    query: str,
    doc_id: str | None = None,
    dataset_id: str | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    del doc_id, dataset_id, context  # not needed for single-report v1, kept for payload compatibility.

    runtime.ensure_index_ready()
    retrieval = creditcard_chat_turn(
        query=query,
        db_path=runtime.config.db_path,
        report_id=runtime.config.report_id,
        limit=8,
    )
    evidence = retrieval.get("evidence", [])
    mode = retrieval.get("mode", "unknown")
    deterministic_answer = retrieval.get("answer", "")

    llm_used = False
    llm_error = None
    answer = deterministic_answer
    try:
        answer = synthesize_with_ollama(
            query=query,
            mode=mode,
            evidence=evidence,
            config=runtime.config.ollama,
        )
        llm_used = True
    except OllamaError as exc:
        llm_error = str(exc)
        if not runtime.config.allow_deterministic_fallback:
            raise

    return {
        "answer": answer,
        "mode": mode,
        "citations": _citations_from_evidence(evidence),
        "debug": {
            "llm_used": llm_used,
            "llm_error": llm_error,
            "fallback_used": bool(llm_error and runtime.config.allow_deterministic_fallback),
            "evidence_count": len(evidence),
            "report_id": runtime.config.report_id,
        },
    }
