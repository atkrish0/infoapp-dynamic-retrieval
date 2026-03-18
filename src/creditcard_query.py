from __future__ import annotations

import re
import sqlite3
from calendar import month_name
from dataclasses import dataclass
from datetime import datetime
from typing import Any


TOKEN_RE = re.compile(r"[a-zA-Z0-9_/$.-]+")
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "for",
    "from",
    "how",
    "in",
    "is",
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
class QueryContext:
    query: str
    report_id: str
    mode: str
    answer: str
    evidence: list[dict[str, Any]]


def _tokenize(text: str) -> list[str]:
    tokens = []
    for token in TOKEN_RE.findall(text.lower()):
        if len(token) < 2 or token in STOPWORDS:
            continue
        tokens.append(token)
    return tokens


def _normalize_date_str(text: str) -> str | None:
    text = text.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _extract_exact_date(query: str) -> str | None:
    q = query.strip()
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", q)
    if m:
        return _normalize_date_str(m.group(1))
    m = re.search(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b", q)
    if m:
        return _normalize_date_str(m.group(1))
    return None


def _extract_month_year_prefix(query: str) -> str | None:
    q = query.lower()
    month_map = {name.lower(): idx for idx, name in enumerate(month_name) if name}
    for m_name, m_idx in month_map.items():
        m = re.search(rf"\b{m_name}\s+(\d{{4}})\b", q)
        if m:
            year = int(m.group(1))
            return f"{year:04d}-{m_idx:02d}"
    m = re.search(r"\b(\d{4})-(\d{2})\b", q)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
    return None


def _extract_year(query: str) -> str | None:
    m = re.search(r"\b(20\d{2}|19\d{2})\b", query)
    if m:
        return m.group(1)
    return None


def _is_aggregate(query: str) -> bool:
    q = query.lower()
    return any(w in q for w in ["sum", "total", "average", "avg", "count"])


def _wants_count(query: str) -> bool:
    q = query.lower()
    return "count" in q or "how many" in q


def _wants_avg(query: str) -> bool:
    q = query.lower()
    return "average" in q or "avg" in q


def _metric_flags(query: str) -> tuple[bool, bool]:
    q = query.lower()
    charge = any(w in q for w in ["charge", "charges", "spend", "purchase", "spent"])
    payment = any(w in q for w in ["payment", "payments", "paid"])
    if not charge and not payment and _is_aggregate(q):
        # default aggregate metric for credit-card insight questions
        charge = True
    return charge, payment


def _distinct(conn: sqlite3.Connection, report_id: str, column: str) -> list[str]:
    rows = conn.execute(
        f"SELECT DISTINCT {column} FROM transactions WHERE report_id = ? AND {column} IS NOT NULL",
        (report_id,),
    ).fetchall()
    return [str(r[0]).strip() for r in rows if str(r[0]).strip()]


def _extract_dimension_filters(
    conn: sqlite3.Connection,
    query: str,
    report_id: str,
) -> dict[str, list[str]]:
    q = query.lower()
    filters: dict[str, list[str]] = {"payee_name": [], "category": [], "tag": [], "personal_business": []}
    for col in ["payee_name", "category", "tag", "personal_business"]:
        for value in _distinct(conn, report_id, col):
            if value.lower() in q:
                filters[col].append(value)
    return filters


def _build_where(
    report_id: str,
    *,
    exact_date: str | None,
    month_prefix: str | None,
    year: str | None,
    filters: dict[str, list[str]],
) -> tuple[str, list[Any]]:
    clauses = ["report_id = ?"]
    params: list[Any] = [report_id]
    if exact_date:
        clauses.append("date = ?")
        params.append(exact_date)
    elif month_prefix:
        clauses.append("date LIKE ?")
        params.append(month_prefix + "%")
    elif year:
        clauses.append("date LIKE ?")
        params.append(year + "%")

    for col, values in filters.items():
        if not values:
            continue
        placeholders = ",".join("?" for _ in values)
        clauses.append(f"{col} IN ({placeholders})")
        params.extend(values)

    return " WHERE " + " AND ".join(clauses), params


def _aggregate_query(
    conn: sqlite3.Connection,
    query: str,
    report_id: str,
) -> QueryContext | None:
    if not _is_aggregate(query):
        return None

    exact_date = _extract_exact_date(query)
    month_prefix = _extract_month_year_prefix(query)
    year = _extract_year(query)
    filters = _extract_dimension_filters(conn, query, report_id)
    where_sql, params = _build_where(
        report_id,
        exact_date=exact_date,
        month_prefix=month_prefix,
        year=year,
        filters=filters,
    )

    wants_count = _wants_count(query)
    wants_avg = _wants_avg(query)
    wants_sum = any(w in query.lower() for w in ["sum", "total"])
    wants_charge, wants_payment = _metric_flags(query)

    select_parts = []
    if wants_count:
        select_parts.append("COUNT(*) AS txn_count")
    # Count-only request should stay focused on count unless a metric request is explicit.
    include_metric = (not wants_count) or wants_avg or wants_sum
    if wants_avg and include_metric:
        if wants_charge:
            select_parts.append("AVG(COALESCE(charge,0)) AS avg_charge")
        if wants_payment:
            select_parts.append("AVG(COALESCE(payment,0)) AS avg_payment")
    elif include_metric:
        if wants_charge:
            select_parts.append("SUM(COALESCE(charge,0)) AS total_charge")
        if wants_payment:
            select_parts.append("SUM(COALESCE(payment,0)) AS total_payment")
    if not select_parts:
        return None

    row = conn.execute(
        f"SELECT {', '.join(select_parts)} FROM transactions {where_sql}",
        params,
    ).fetchone()
    if row is None:
        return None

    row_dict = dict(row)
    summary_parts = []
    for key, value in row_dict.items():
        if value is None:
            continue
        if key.startswith("total_") or key.startswith("avg_"):
            summary_parts.append(f"{key}={float(value):,.2f}")
        else:
            summary_parts.append(f"{key}={int(value)}")

    if not summary_parts:
        return None

    answer = "SQL aggregate result: " + ", ".join(summary_parts)
    evidence = []
    return QueryContext(query=query, report_id=report_id, mode="sql_agg", answer=answer, evidence=evidence)


def _rows_query(
    conn: sqlite3.Connection,
    query: str,
    report_id: str,
    limit: int,
) -> QueryContext | None:
    exact_date = _extract_exact_date(query)
    month_prefix = _extract_month_year_prefix(query)
    year = _extract_year(query)
    filters = _extract_dimension_filters(conn, query, report_id)
    where_sql, params = _build_where(
        report_id,
        exact_date=exact_date,
        month_prefix=month_prefix,
        year=year,
        filters=filters,
    )
    if where_sql.strip() == "WHERE report_id = ?":
        return None

    rows = conn.execute(
        f"""
        SELECT id, row_num, date, payee_name, category, tag, personal_business, charge, payment
        FROM transactions
        {where_sql}
        ORDER BY date, row_num
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    if not rows:
        return None

    evidence = []
    for r in rows:
        snippet = (
            f"row {r['row_num']} date={r['date']} payee={r['payee_name']} "
            f"category={r['category']} tag={r['tag']} "
            f"charge={r['charge']} payment={r['payment']}"
        )
        evidence.append(
            {
                "id": r["id"],
                "doc_id": f"{report_id}.xlsx",
                "chunk_type": "row",
                "row_num": r["row_num"],
                "source_path": "excel",
                "snippet": snippet,
                "score": 1.0,
            }
        )
    answer = "SQL row match result from underlying Excel-backed table."
    return QueryContext(query=query, report_id=report_id, mode="sql_rows", answer=answer, evidence=evidence)


def _fts_query(
    conn: sqlite3.Connection,
    query: str,
    report_id: str,
    limit: int,
) -> QueryContext:
    tokens = _tokenize(query)
    if not tokens:
        return QueryContext(
            query=query,
            report_id=report_id,
            mode="insufficient",
            answer="Not enough query signal. Please ask with a field, vendor, date, category, tag, or metric.",
            evidence=[],
        )
    match = " OR ".join(f'"{t.replace(chr(34), chr(34) * 2)}"' for t in tokens[:12])
    rows = conn.execute(
        """
        SELECT t.id, t.row_num, t.date, t.payee_name, t.category, t.tag, t.charge, t.payment,
               bm25(f) AS rank
        FROM transactions_fts f
        JOIN transactions t ON t.id = f.row_id
        WHERE f.content_text MATCH ? AND t.report_id = ?
        ORDER BY rank
        LIMIT ?
        """,
        (match, report_id, limit),
    ).fetchall()
    if not rows:
        return QueryContext(
            query=query,
            report_id=report_id,
            mode="insufficient",
            answer="No matching rows found in the Excel-backed SQL index.",
            evidence=[],
        )

    evidence = []
    for r in rows:
        rank = abs(float(r["rank"])) if r["rank"] is not None else 100.0
        score = round(1.0 / (1.0 + rank), 4)
        snippet = (
            f"row {r['row_num']} date={r['date']} payee={r['payee_name']} "
            f"category={r['category']} tag={r['tag']} "
            f"charge={r['charge']} payment={r['payment']}"
        )
        evidence.append(
            {
                "id": r["id"],
                "doc_id": f"{report_id}.xlsx",
                "chunk_type": "row",
                "row_num": r["row_num"],
                "source_path": "excel",
                "snippet": snippet,
                "score": score,
            }
        )

    answer = "FTS match result from underlying Excel-backed SQL index."
    return QueryContext(query=query, report_id=report_id, mode="fts_rows", answer=answer, evidence=evidence)


def creditcard_chat_turn(
    query: str,
    db_path: str,
    *,
    report_id: str = "creditcard",
    limit: int = 8,
) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        agg = _aggregate_query(conn, query, report_id)
        if agg is not None:
            return {
                "answer": agg.answer,
                "mode": agg.mode,
                "evidence": agg.evidence,
                "query": query,
                "report_id": report_id,
            }

        rows = _rows_query(conn, query, report_id, limit)
        if rows is not None:
            return {
                "answer": rows.answer,
                "mode": rows.mode,
                "evidence": rows.evidence,
                "query": query,
                "report_id": report_id,
            }

        fts = _fts_query(conn, query, report_id, limit)
        return {
            "answer": fts.answer,
            "mode": fts.mode,
            "evidence": fts.evidence,
            "query": query,
            "report_id": report_id,
        }
    finally:
        conn.close()
