from __future__ import annotations

from typing import Any

from .retriever import retrieve


def _format_evidence_line(item: dict[str, Any]) -> str:
    row_suffix = f" row={item['row_num']}" if item.get("row_num") is not None else ""
    return (
        f"[{item['doc_id']} | {item['chunk_type']}{row_suffix}] "
        f"{item['snippet']}"
    )


def _build_answer(mode: str, evidence: list[dict[str, Any]]) -> str:
    if not evidence:
        return "Not enough indexed evidence. No close matches were found."

    top_lines = [_format_evidence_line(item) for item in evidence[:3]]

    if mode == "current_doc":
        header = "Answer from current report:"
    elif mode == "cross_doc":
        header = "Current report was insufficient; using cross-report context."
    else:
        header = "Not enough indexed evidence. Closest matches:"

    return header + "\n- " + "\n- ".join(top_lines)


def chat_turn(query: str, current_doc_id: str, db_path: str) -> dict[str, Any]:
    """
    One deterministic retrieval-grounded chat turn.
    """
    retrieval = retrieve(query=query, current_doc_id=current_doc_id, db_path=db_path, k=8)
    mode = retrieval["mode"]
    evidence = retrieval["evidence"]
    answer = _build_answer(mode, evidence)
    return {
        "answer": answer,
        "mode": mode,
        "evidence": evidence,
    }
