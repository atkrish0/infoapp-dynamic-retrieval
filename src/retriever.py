from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any


TOKEN_RE = re.compile(r"[a-zA-Z0-9_/$.-]+")
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "around",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "me",
    "of",
    "on",
    "or",
    "show",
    "that",
    "the",
    "this",
    "to",
    "what",
    "which",
    "with",
}


@dataclass
class RetrievalThreshold:
    min_hits: int = 2
    min_top_score: float = 0.26
    min_avg_top2_score: float = 0.2


def _tokenize(text: str) -> list[str]:
    out = []
    for token in TOKEN_RE.findall(text.lower()):
        if len(token) < 2:
            continue
        if token in STOPWORDS:
            continue
        out.append(token)
    return out


def _fts_query(tokens: list[str]) -> str:
    if not tokens:
        return '""'
    escaped = [t.replace('"', '""') for t in tokens]
    return " OR ".join(f'"{t}"' for t in escaped[:12])


def _snippet(text: str, max_len: int = 260) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= max_len:
        return compact
    return compact[: max_len - 3] + "..."


def _compute_score(row: sqlite3.Row, tokens: list[str]) -> float:
    rank = row["rank"]
    rank_abs = abs(float(rank)) if rank is not None else 100.0
    base = 1.0 / (1.0 + rank_abs)

    chunk_type = (row["chunk_type"] or "").lower()
    section = (row["section"] or "").lower()
    content_text = (row["content_text"] or "").lower()
    token_set = set(tokens)
    boost = 0.0

    for token in token_set:
        if token in chunk_type or token in section:
            boost += 0.14
        elif token in content_text:
            boost += 0.02

    if chunk_type == "chart" and token_set.intersection({"chart", "bar", "pie", "line"}):
        boost += 0.18
    if chunk_type == "schema" and token_set.intersection({"column", "columns", "field", "schema"}):
        boost += 0.16
    if chunk_type == "row" and token_set.intersection({"row", "data", "value", "date", "charge"}):
        boost += 0.14

    score_hint = float(row["score_hint"] or 0.0) * 0.08
    return base + boost + score_hint


def _execute_search(
    conn: sqlite3.Connection,
    *,
    query: str,
    tokens: list[str],
    doc_id: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    q = _fts_query(tokens)
    params: list[Any] = [q]
    where = ""
    if doc_id:
        where = "AND c.doc_id = ?"
        params.append(doc_id)
    params.append(limit)

    sql = f"""
        SELECT
            c.id,
            c.doc_id,
            c.chunk_type,
            c.section,
            c.content_text,
            c.row_num,
            c.score_hint,
            d.source_path,
            bm25(chunks_fts) AS rank
        FROM chunks_fts
        JOIN chunks c ON c.id = chunks_fts.rowid
        JOIN documents d ON d.doc_id = c.doc_id
        WHERE chunks_fts MATCH ?
        {where}
        ORDER BY rank
        LIMIT ?
    """

    try:
        rows = list(conn.execute(sql, params))
    except sqlite3.OperationalError:
        # Fallback for problematic tokenization; still deterministic.
        safe_query = re.sub(r"[^\w\s]", " ", query).strip()
        like = f"%{safe_query}%" if safe_query else "%"
        params2: list[Any] = [like]
        where2 = ""
        if doc_id:
            where2 = "AND c.doc_id = ?"
            params2.append(doc_id)
        params2.append(limit)
        rows = list(
            conn.execute(
                f"""
                SELECT
                    c.id,
                    c.doc_id,
                    c.chunk_type,
                    c.section,
                    c.content_text,
                    c.row_num,
                    c.score_hint,
                    d.source_path,
                    50.0 AS rank
                FROM chunks c
                JOIN documents d ON d.doc_id = c.doc_id
                WHERE c.content_text LIKE ?
                {where2}
                LIMIT ?
                """,
                params2,
            )
        )

    results: list[dict[str, Any]] = []
    for row in rows:
        score = _compute_score(row, tokens)
        results.append(
            {
                "id": row["id"],
                "doc_id": row["doc_id"],
                "chunk_type": row["chunk_type"],
                "section": row["section"],
                "snippet": _snippet(row["content_text"]),
                "row_num": row["row_num"],
                "source_path": row["source_path"],
                "score": round(score, 4),
            }
        )
    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def _is_sufficient(results: list[dict[str, Any]], t: RetrievalThreshold) -> bool:
    if not results:
        return False
    if len(results) >= t.min_hits:
        top2 = results[:2]
        avg_top2 = sum(r["score"] for r in top2) / len(top2)
        return results[0]["score"] >= t.min_top_score or avg_top2 >= t.min_avg_top2_score
    return results[0]["score"] >= (t.min_top_score + 0.2)


def _dedupe_by_id(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in results:
        row_id = int(item["id"])
        if row_id in seen:
            continue
        seen.add(row_id)
        out.append(item)
    return out


def retrieve(query: str, current_doc_id: str, db_path: str, k: int = 8) -> dict[str, Any]:
    """
    Retrieve evidence using current-doc first, then cross-doc fallback.
    """
    tokens = _tokenize(query)
    threshold = RetrievalThreshold()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        local = _execute_search(
            conn,
            query=query,
            tokens=tokens,
            doc_id=current_doc_id,
            limit=max(k, 6),
        )
        if _is_sufficient(local, threshold):
            return {
                "query": query,
                "current_doc_id": current_doc_id,
                "mode": "current_doc",
                "evidence": local[:k],
            }

        global_results = _execute_search(
            conn,
            query=query,
            tokens=tokens,
            doc_id=None,
            limit=max(2 * k, 16),
        )

        merged = _dedupe_by_id(local + global_results)
        merged.sort(key=lambda x: x["score"], reverse=True)
        evidence = merged[:k]

        cross_candidates = [item for item in global_results if item["doc_id"] != current_doc_id]
        has_cross_doc = any(item["doc_id"] != current_doc_id for item in evidence)
        global_is_sufficient = _is_sufficient(cross_candidates, threshold)
        if has_cross_doc and evidence and global_is_sufficient:
            mode = "cross_doc"
        elif evidence:
            mode = "insufficient"
        else:
            mode = "insufficient"

        return {
            "query": query,
            "current_doc_id": current_doc_id,
            "mode": mode,
            "evidence": evidence,
        }
    finally:
        conn.close()
