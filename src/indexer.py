from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from bs4 import BeautifulSoup


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _index_to_alpha(index: int) -> str:
    out = []
    i = index
    while True:
        i, rem = divmod(i, 26)
        out.append(chr(ord("a") + rem))
        if i == 0:
            break
        i -= 1
    return "".join(reversed(out))


def _json_dumps_compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _textify(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, str):
        return value
    return _json_dumps_compact(value)


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS documents (
            doc_id TEXT PRIMARY KEY,
            source_path TEXT NOT NULL,
            source_type TEXT NOT NULL,
            title TEXT,
            dataset_id TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id TEXT NOT NULL,
            chunk_type TEXT NOT NULL,
            section TEXT,
            content_text TEXT NOT NULL,
            content_json TEXT,
            row_num INTEGER,
            score_hint REAL DEFAULT 0.0,
            FOREIGN KEY(doc_id) REFERENCES documents(doc_id)
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            content_text,
            content='chunks',
            content_rowid='id'
        );

        CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
            INSERT INTO chunks_fts(rowid, content_text)
            VALUES (new.id, new.content_text);
        END;

        CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
            INSERT INTO chunks_fts(chunks_fts, rowid, content_text)
            VALUES('delete', old.id, old.content_text);
        END;

        CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
            INSERT INTO chunks_fts(chunks_fts, rowid, content_text)
            VALUES('delete', old.id, old.content_text);
            INSERT INTO chunks_fts(rowid, content_text)
            VALUES (new.id, new.content_text);
        END;
        """
    )
    conn.commit()


def _insert_document(
    conn: sqlite3.Connection,
    *,
    doc_id: str,
    source_path: str,
    source_type: str,
    title: str | None,
    dataset_id: str | None,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO documents
            (doc_id, source_path, source_type, title, dataset_id, updated_at)
        VALUES
            (?, ?, ?, ?, ?, ?)
        """,
        (doc_id, source_path, source_type, title, dataset_id, _utc_now()),
    )


def _insert_chunk(
    conn: sqlite3.Connection,
    *,
    doc_id: str,
    chunk_type: str,
    section: str,
    content_text: str,
    content_json: Any | None = None,
    row_num: int | None = None,
    score_hint: float = 0.0,
) -> None:
    conn.execute(
        """
        INSERT INTO chunks
            (doc_id, chunk_type, section, content_text, content_json, row_num, score_hint)
        VALUES
            (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            doc_id,
            chunk_type,
            section,
            content_text,
            _json_dumps_compact(content_json) if content_json is not None else None,
            row_num,
            score_hint,
        ),
    )


def _prepare_alias_map(columns: list[dict[str, Any]]) -> dict[str, str]:
    alias_map: dict[str, str] = {}
    for idx, col in enumerate(columns):
        col_name = (col.get("title") or col.get("field") or f"column_{idx + 1}").strip()
        letter = _index_to_alpha(idx)
        base_aliases = [
            letter,
            f"$m${letter}",
            col.get("field"),
            col.get("title"),
        ]
        expanded_aliases: list[str] = []
        for alias in base_aliases:
            if not alias:
                continue
            expanded_aliases.extend([alias, f"{alias}$org", f"{alias}$date"])
        for alias in expanded_aliases:
            alias_map[str(alias).lower()] = col_name
    return alias_map


def _normalize_row_key(raw_key: str) -> tuple[str, str]:
    key = raw_key.lower()
    suffix = ""
    if key.endswith("$org"):
        suffix = " (raw)"
        key = key[:-4]
    elif key.endswith("$date"):
        suffix = " (date)"
        key = key[:-5]
    return key, suffix


def _iter_json_files(json_dir: Path) -> Iterable[Path]:
    return sorted(p for p in json_dir.glob("*.json") if p.is_file())


def _iter_html_files(html_dir: Path) -> Iterable[Path]:
    return sorted(p for p in html_dir.glob("*.html") if p.is_file())


def _index_json_report(conn: sqlite3.Connection, path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    doc_id = path.name
    data_files = payload.get("dataFiles") or []
    first_df = data_files[0] if data_files else {}
    dataset_id = (
        first_df.get("id")
        or first_df.get("name")
        or first_df.get("title")
        or "Sheet_1"
    )
    title = payload.get("projectName") or payload.get("filePath") or doc_id
    _insert_document(
        conn,
        doc_id=doc_id,
        source_path=str(path),
        source_type="json",
        title=title,
        dataset_id=dataset_id,
    )

    project_meta = {
        "projectName": payload.get("projectName"),
        "version": payload.get("version"),
        "htmlTemplate": payload.get("htmlTemplate"),
        "filePath": payload.get("filePath"),
    }
    _insert_chunk(
        conn,
        doc_id=doc_id,
        chunk_type="meta",
        section="project",
        content_text=(
            f"project={project_meta.get('projectName')} "
            f"version={project_meta.get('version')} "
            f"dataset_id={dataset_id}"
        ),
        content_json=project_meta,
        score_hint=0.45,
    )

    columns = first_df.get("columns") or []
    columns_list: list[dict[str, Any]] = [
        c if isinstance(c, dict) else {"title": str(c)} for c in columns
    ]
    alias_map = _prepare_alias_map(columns_list)

    for idx, col in enumerate(columns_list):
        col_name = col.get("title") or col.get("field") or f"column_{idx + 1}"
        field = col.get("field") or _index_to_alpha(idx)
        content_text = (
            f"schema column={col_name} field={field} "
            f"format={col.get('format')} type={col.get('type')}"
        )
        _insert_chunk(
            conn,
            doc_id=doc_id,
            chunk_type="schema",
            section="columns",
            content_text=content_text,
            content_json=col,
            score_hint=0.55,
        )

    info_elements = payload.get("infoElements") or {}
    root_tags = info_elements.get("rootTags") or []
    chart_count = 0
    for tag in root_tags:
        if not isinstance(tag, dict):
            continue
        if str(tag.get("type", "")).lower() != "chart":
            continue
        chart_count += 1
        chart_type = tag.get("chartType") or "unknown"
        dimension = tag.get("dimension")
        measure = tag.get("measure")
        text = (
            f"chart type={chart_type} title={tag.get('title', '')} "
            f"dimension={dimension} measure={measure} "
            f"legend={tag.get('showLegend')}"
        )
        _insert_chunk(
            conn,
            doc_id=doc_id,
            chunk_type="chart",
            section="rootTags",
            content_text=text,
            content_json=tag,
            score_hint=0.7,
        )

    _insert_chunk(
        conn,
        doc_id=doc_id,
        chunk_type="meta",
        section="summary",
        content_text=(
            f"summary rows={len(first_df.get('data') or [])} "
            f"columns={len(columns_list)} charts={chart_count} "
            f"dataset_id={dataset_id}"
        ),
        content_json={
            "rows": len(first_df.get("data") or []),
            "columns": len(columns_list),
            "charts": chart_count,
            "dataset_id": dataset_id,
        },
        score_hint=0.5,
    )

    rows = first_df.get("data") or []
    for row_idx, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            row_text = f"row {row_idx} value={_textify(row)}"
            row_json = {"value": row}
        else:
            pairs: list[str] = []
            for key, value in row.items():
                key_norm, suffix = _normalize_row_key(key)
                canonical = alias_map.get(key_norm, key)
                pairs.append(f"{canonical}{suffix}={_textify(value)}")
            row_text = "row " + str(row_idx) + " " + " | ".join(pairs)
            row_json = row
        _insert_chunk(
            conn,
            doc_id=doc_id,
            chunk_type="row",
            section="dataset",
            content_text=row_text,
            content_json=row_json,
            row_num=row_idx,
            score_hint=0.35,
        )


def _index_html_report(conn: sqlite3.Connection, path: Path) -> None:
    html = path.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(html, "lxml")
    doc_id = path.name

    title = soup.title.text.strip() if soup.title and soup.title.text else doc_id
    meta_doc = soup.find("meta", attrs={"name": "report-doc-id"})
    meta_dataset = soup.find("meta", attrs={"name": "report-dataset-id"})
    meta_desc = soup.find("meta", attrs={"name": "description"})

    dataset_id = meta_dataset.get("content").strip() if meta_dataset else None
    report_doc_hint = meta_doc.get("content").strip() if meta_doc else None
    description = meta_desc.get("content").strip() if meta_desc else ""
    script_count = len(soup.find_all("script"))

    _insert_document(
        conn,
        doc_id=doc_id,
        source_path=str(path),
        source_type="html",
        title=title,
        dataset_id=dataset_id,
    )

    summary = (
        f"title={title} report_doc_id_hint={report_doc_hint} dataset_id={dataset_id} "
        f"description={description} scripts={script_count}"
    )
    _insert_chunk(
        conn,
        doc_id=doc_id,
        chunk_type="meta",
        section="html",
        content_text=summary,
        content_json={
            "title": title,
            "report_doc_id_hint": report_doc_hint,
            "dataset_id": dataset_id,
            "description": description,
            "script_count": script_count,
        },
        score_hint=0.4,
    )

    lower_html = html.lower()
    cues = {
        "has_inject_widget_js": "inject_widget.js" in lower_html,
        "has_info_object": "var infoobject=" in lower_html,
        "has_info_data": "var infodata=" in lower_html,
        "has_interactive_info": "new interactiveinfo" in lower_html,
    }
    cue_text = "html cues " + " ".join(f"{k}={v}" for k, v in cues.items())
    _insert_chunk(
        conn,
        doc_id=doc_id,
        chunk_type="html_structure",
        section="signals",
        content_text=cue_text,
        content_json=cues,
        score_hint=0.42,
    )


def build_index(json_dir: str, html_dir: str, db_path: str) -> None:
    """
    Build a fresh SQLite + FTS5 index for report JSON/HTML files.
    """
    json_path = Path(json_dir)
    html_path = Path(html_dir)
    db = Path(db_path)
    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists():
        db.unlink()

    conn = sqlite3.connect(str(db))
    try:
        _ensure_schema(conn)

        for path in _iter_json_files(json_path):
            _index_json_report(conn, path)

        for path in _iter_html_files(html_path):
            _index_html_report(conn, path)

        conn.commit()
    finally:
        conn.close()
