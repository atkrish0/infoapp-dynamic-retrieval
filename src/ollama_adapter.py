from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


class OllamaError(RuntimeError):
    pass


@dataclass
class OllamaConfig:
    base_url: str = "http://localhost:11434"
    model: str = "llama3.1"
    timeout_sec: int = 45


def _normalize_base(base_url: str) -> str:
    return base_url.rstrip("/")


def check_ollama_health(config: OllamaConfig) -> dict[str, Any]:
    """
    Returns a status dict that can be used by /health.
    """
    base = _normalize_base(config.base_url)
    req = urllib.request.Request(f"{base}/api/tags", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=max(5, config.timeout_sec // 3)) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {
            "ok": False,
            "base_url": config.base_url,
            "model": config.model,
            "error": str(exc),
        }

    models = [m.get("name", "") for m in payload.get("models", []) if isinstance(m, dict)]
    model_available = any(name == config.model or name.startswith(f"{config.model}:") for name in models)
    return {
        "ok": True,
        "base_url": config.base_url,
        "model": config.model,
        "model_available": model_available,
        "model_count": len(models),
    }


def _prompt_from_evidence(query: str, mode: str, evidence: list[dict[str, Any]]) -> str:
    lines = []
    for item in evidence[:12]:
        row_num = item.get("row_num")
        row_txt = f"row={row_num}" if row_num is not None else "row=na"
        lines.append(f"[{row_txt}] {item.get('snippet', '')}")

    evidence_block = "\n".join(lines) if lines else "(no evidence)"
    return (
        "You are a grounded credit-card report assistant.\n"
        "Answer using only the evidence rows provided.\n"
        "If evidence is missing, explicitly say that data is insufficient.\n"
        "When you reference facts, include row citations in square brackets, e.g. [row=12].\n\n"
        f"Retrieval mode: {mode}\n"
        f"User query: {query}\n\n"
        f"Evidence:\n{evidence_block}\n"
    )


def synthesize_with_ollama(
    *,
    query: str,
    mode: str,
    evidence: list[dict[str, Any]],
    config: OllamaConfig,
) -> str:
    base = _normalize_base(config.base_url)
    prompt = _prompt_from_evidence(query=query, mode=mode, evidence=evidence)
    payload = {
        "model": config.model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/api/generate",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=config.timeout_sec) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="ignore")
        raise OllamaError(f"Ollama HTTP {exc.code}: {details}") from exc
    except Exception as exc:
        raise OllamaError(f"Ollama request failed: {exc}") from exc

    text = str(body.get("response", "")).strip()
    if not text:
        raise OllamaError("Ollama returned an empty response")
    return text
