"""受控 SELECT 执行：表白名单、属主列注入、PyMySQL 安全执行；预检可插拔。"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import pymysql
from pymysql.cursors import DictCursor

from app.core.settings import load_settings
from app.db_access.catalog import TableCatalog, get_default_catalog

SELECT_PATTERN = re.compile(r"^\s*select\b", re.IGNORECASE)
FROM_TABLE_PATTERN = re.compile(
    r"\bfrom\s+(?:`([^`]+)`|([a-zA-Z_][a-zA-Z0-9_]*))(?=\s|$|;|\))",
    re.IGNORECASE,
)
JOIN_TABLE_PATTERN = re.compile(
    r"\bjoin\s+(?:`([^`]+)`|([a-zA-Z_][a-zA-Z0-9_]*))(?=\s|$|;|\))",
    re.IGNORECASE,
)
LIMIT_PATTERN = re.compile(r"\blimit\s+\d+\s*$", re.IGNORECASE)
_WINDOW_OVER_PATTERN = re.compile(r"\bOVER\s*\(", re.IGNORECASE)
_USER_ID_EQ_PATTERN = re.compile(
    r"\buser_id\s*=\s*(?:'([^']*)'|\"([^\"]*)\"|(\d+))",
    re.IGNORECASE,
)

# 预检：可 `register_sql_preflight_check` 追加，或测试时替换
PREFLIGHT_SQL_CHECKS: list[Callable[[str], None]] = []


def register_sql_preflight_check(fn: Callable[[str], None]) -> None:
    PREFLIGHT_SQL_CHECKS.append(fn)


def _preflight_no_window_function(sql: str) -> None:
    if _WINDOW_OVER_PATTERN.search(sql or ""):
        raise ValueError("window_function_not_allowed")


def _run_preflight_sql_checks(sql: str) -> None:
    for fn in PREFLIGHT_SQL_CHECKS:
        fn(sql)


# 模块默认预检
PREFLIGHT_SQL_CHECKS.append(_preflight_no_window_function)


def validate_sql_before_execute(sql: str) -> None:
    """与历史 API 兼容：执行全部已注册预检。"""
    _run_preflight_sql_checks(sql)


def _extract_table_name(sql: str) -> str | None:
    m = FROM_TABLE_PATTERN.search(sql)
    if not m:
        return None
    name = (m.group(1) or m.group(2) or "").strip()
    return name.lower() if name else None


def _validate_user_id_literals_match_session(sql: str, user_id: str) -> None:
    uid = str(user_id).strip()
    for m in _USER_ID_EQ_PATTERN.finditer(sql):
        raw = m.group(1) if m.group(1) is not None else (m.group(2) if m.group(2) is not None else m.group(3))
        if raw is None:
            continue
        if str(raw).strip() != uid:
            raise ValueError(f"user_scope_literal_mismatch:user_id={raw}")


def _qualify_owner_column(sql: str, table_name: str, owner_col: str) -> str:
    if not re.search(r"\bjoin\b", sql, re.IGNORECASE):
        return owner_col
    m = re.search(
        r"\bfrom\s+(?:`([^`]+)`|([a-zA-Z_][a-zA-Z0-9_]*))"
        r"\s+(?:as\s+)?(?:`([^`]+)`|([a-zA-Z_][a-zA-Z0-9_]*))\s+"
        r"(?:(?:natural|inner|left|right|cross|straight)(?:\s+outer)?\s+)*join\b",
        sql,
        re.IGNORECASE,
    )
    if m:
        alias = (m.group(3) or m.group(4) or "").strip()
        if alias:
            return f"{alias}.{owner_col}"
    m = re.search(
        r"\bfrom\s+(?:`([^`]+)`|([a-zA-Z_][a-zA-Z0-9_]*))\s+"
        r"(?:(?:natural|inner|left|right|cross|straight)(?:\s+outer)?\s+)*join\b",
        sql,
        re.IGNORECASE,
    )
    if m:
        if m.group(1) is not None:
            return f"`{m.group(1)}`.{owner_col}"
        return f"{m.group(2)}.{owner_col}"
    return owner_col


def _first_clause_after_select(sql: str) -> int:
    clause_kw = [
        r"\bgroup\s+by\b",
        r"\bhaving\b",
        r"\border\s+by\b",
        r"\blimit\b",
    ]
    insert_at = len(sql)
    for pat in clause_kw:
        m = re.search(pat, sql, re.IGNORECASE)
        if m and m.start() < insert_at:
            insert_at = m.start()
    return insert_at


def _enforce_user_scope(sql: str, table_name: str, user_id: str, catalog: TableCatalog) -> tuple[str, list[Any]]:
    spec = catalog.get(table_name)
    if spec is None:
        raise ValueError(f"table_not_allowed:{table_name}")

    if not SELECT_PATTERN.search(sql):
        raise ValueError("only_select_allowed")

    if spec.owner_column is None:
        return sql, []

    owner_col = _qualify_owner_column(sql, table_name, spec.owner_column)
    insert_at = _first_clause_after_select(sql)
    has_where = re.search(r"\bwhere\b", sql, re.IGNORECASE) is not None

    if has_where:
        head = sql[:insert_at].rstrip()
        tail = sql[insert_at:].lstrip()
        if tail:
            return f"{head} AND {owner_col} = %s {tail}", [user_id]
        return f"{head} AND {owner_col} = %s", [user_id]

    if insert_at < len(sql):
        head = sql[:insert_at].rstrip()
        tail = sql[insert_at:].lstrip()
        return f"{head} WHERE {owner_col} = %s {tail}", [user_id]
    return f"{sql.rstrip()} WHERE {owner_col} = %s", [user_id]


def _append_limit(sql: str, max_rows: int) -> str:
    if LIMIT_PATTERN.search(sql):
        return sql
    return f"{sql} LIMIT {max_rows}"


def is_valid_select_with_from(sql: str) -> bool:
    if not SELECT_PATTERN.search(sql or ""):
        return False
    return _extract_table_name(sql or "") is not None


def extract_sql_primary_table(sql: str) -> str | None:
    return _extract_table_name(sql)


def _normalize_sql_statement(sql: str) -> str:
    s = sql.strip()
    while s.endswith(";"):
        s = s[:-1].rstrip()
    return s


def _fix_bare_session_user_equals(sql: str, user_id: str) -> str:
    uid = str(user_id).strip()
    if not uid or uid.isdigit():
        return sql
    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", uid):
        return sql
    esc = uid.replace("\\", "\\\\").replace("'", "''")
    pat = re.compile(
        rf"(?P<lhs>\b(?:[a-zA-Z_][a-zA-Z0-9_]*\.)?user_id)\s*=\s*(?!['\"])(?P<rhs>{re.escape(uid)})\b",
        re.IGNORECASE,
    )
    return pat.sub(rf"\g<lhs> = '{esc}'", sql)


def _escape_percent_for_pymysql(sql: str) -> str:
    if "%s" not in sql:
        return sql.replace("%", "%%")
    parts = sql.split("%s")
    escaped = [p.replace("%", "%%") for p in parts]
    return "%s".join(escaped)


def execute_user_scoped_sql(
    sql: str,
    user_id: str,
    catalog: TableCatalog | None = None,
) -> list[dict[str, Any]]:
    cat = catalog or get_default_catalog()
    settings = load_settings()
    sql = _normalize_sql_statement(sql)
    sql = _fix_bare_session_user_equals(sql, user_id)
    if not sql:
        raise ValueError("empty_sql")
    table = _extract_table_name(sql)
    if not table:
        raise ValueError("missing_from_table")
    if cat.get(table) is None:
        raise ValueError(f"table_not_allowed:{table}")
    if ";" in sql:
        raise ValueError("multi_statement_not_allowed")
    _validate_user_id_literals_match_session(sql, user_id)
    validate_sql_before_execute(sql)

    scoped_sql, params = _enforce_user_scope(sql, table, user_id, cat)
    scoped_sql = _append_limit(scoped_sql, settings.sql_max_rows)
    scoped_sql = _escape_percent_for_pymysql(scoped_sql)

    conn = pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=settings.mysql_database,
        cursorclass=DictCursor,
        autocommit=True,
    )
    try:
        with conn.cursor() as cursor:
            cursor.execute(scoped_sql, params)
            rows = cursor.fetchall()
            return list(rows)
    finally:
        conn.close()
