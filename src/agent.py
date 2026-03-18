from __future__ import annotations

import re
from typing import Any

from .chat import _build_answer
from .llm import LLMError, llm_is_configured, synthesize_grounded_answer
from .retriever import retrieve


def _intent(query: str) -> str:
    q = query.lower()
    if any(k in q for k in ["compare", "vs", "versus"]):
        return "compare"
    if any(k in q for k in ["sum", "total", "avg", "average", "count"]):
        return "aggregation"
    if any(k in q for k in ["chart", "plot", "pie", "bar", "line"]):
        return "chart"
    return "general"


def _expanded_query(query: str) -> str:
    q = query
    synonyms = {
        r"\bcategory\b": "category cat tag",
        r"\bpayee\b": "payee name merchant",
        r"\bdescription\b": "description memo",
        r"\bcharge\b": "charge amount value",
    }
    for pattern, replacement in synonyms.items():
        q = re.sub(pattern, replacement, q, flags=re.IGNORECASE)
    return q


def agent_chat_turn(
    query: str,
    current_doc_id: str,
    db_path: str,
    *,
    use_llm: bool = True,
) -> dict[str, Any]:
    """
    Agentic orchestration:
    - tool call: retrieve (current-doc first)
    - optional second-pass query expansion
    - optional LLM grounded synthesis
    """
    trace: list[dict[str, Any]] = []
    detected_intent = _intent(query)

    first = retrieve(query=query, current_doc_id=current_doc_id, db_path=db_path, k=8)
    trace.append(
        {
            "tool": "retrieve",
            "query": query,
            "mode": first["mode"],
            "evidence_count": len(first["evidence"]),
        }
    )

    chosen = first
    if first["mode"] == "insufficient":
        q2 = _expanded_query(query)
        if q2 != query:
            second = retrieve(query=q2, current_doc_id=current_doc_id, db_path=db_path, k=8)
            trace.append(
                {
                    "tool": "retrieve",
                    "query": q2,
                    "mode": second["mode"],
                    "evidence_count": len(second["evidence"]),
                }
            )
            if len(second["evidence"]) > len(first["evidence"]):
                chosen = second

    mode = chosen["mode"]
    evidence = chosen["evidence"]
    answer = _build_answer(mode, evidence)
    llm_used = False
    llm_error: str | None = None

    if use_llm and evidence and llm_is_configured():
        try:
            answer = synthesize_grounded_answer(query=query, mode=mode, evidence=evidence)
            llm_used = True
        except LLMError as exc:
            llm_error = str(exc)

    return {
        "answer": answer,
        "mode": mode,
        "evidence": evidence,
        "intent": detected_intent,
        "trace": trace,
        "llm_used": llm_used,
        "llm_error": llm_error,
    }
