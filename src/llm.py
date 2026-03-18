from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


class LLMError(RuntimeError):
    pass


def llm_is_configured() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


def _evidence_to_prompt(evidence: list[dict[str, Any]]) -> str:
    lines = []
    for idx, item in enumerate(evidence[:12], start=1):
        row = item.get("row_num")
        row_txt = f" row={row}" if row is not None else ""
        lines.append(
            f"{idx}. doc={item['doc_id']} chunk={item['chunk_type']}{row_txt} "
            f"score={item['score']} snippet={item['snippet']}"
        )
    return "\n".join(lines)


def synthesize_grounded_answer(
    *,
    query: str,
    mode: str,
    evidence: list[dict[str, Any]],
    timeout_sec: int = 25,
) -> str:
    """
    Optional LLM synthesis step using retrieved evidence only.
    Requires OPENAI_API_KEY in environment.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise LLMError("OPENAI_API_KEY is not set")

    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    endpoint = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1/responses")

    system = (
        "You are a retrieval-grounded analyst. "
        "Answer only from provided evidence. "
        "Do not invent facts. "
        "If evidence is insufficient, say so explicitly. "
        "Cite relevant evidence using [doc:<doc_id>] inline."
    )
    user = (
        f"Query:\n{query}\n\n"
        f"Retrieval mode:\n{mode}\n\n"
        f"Evidence:\n{_evidence_to_prompt(evidence)}\n\n"
        "Provide a concise answer with citations."
    )

    payload = {
        "model": model,
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": system}]},
            {"role": "user", "content": [{"type": "input_text", "text": user}]},
        ],
        "temperature": 0.0,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="ignore")
        raise LLMError(f"LLM HTTP error: {exc.code} {details}") from exc
    except Exception as exc:
        raise LLMError(f"LLM request failed: {exc}") from exc

    parsed = json.loads(body)
    text = parsed.get("output_text")
    if text and str(text).strip():
        return str(text).strip()

    # Fallback parser for unexpected response shape.
    out = parsed.get("output") or []
    parts: list[str] = []
    for item in out:
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                parts.append(content["text"])
    if parts:
        return "\n".join(parts).strip()

    raise LLMError("LLM response did not contain text output")
