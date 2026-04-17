import re
from typing import Any

import pymysql
from pymysql.cursors import DictCursor

from app.core.settings import load_settings
from app.tools.sql_catalog import SHOP_TABLE_CATALOG


SELECT_PATTERN = re.compile(r"^\s*select\b", re.IGNORECASE)
# FROM `orders` 或 FROM orders（表名用反引号时，反引号与空格之间无 \b，不能依赖尾部 \b）
FROM_TABLE_PATTERN = re.compile(
    r"\bfrom\s+(?:`([^`]+)`|([a-zA-Z_][a-zA-Z0-9_]*))(?=\s|$|;|\))",
    re.IGNORECASE,
)
JOIN_TABLE_PATTERN = re.compile(
    r"\bjoin\s+(?:`([^`]+)`|([a-zA-Z_][a-zA-Z0-9_]*))(?=\s|$|;|\))",
    re.IGNORECASE,
)
LIMIT_PATTERN = re.compile(r"\blimit\s+\d+\s*$", re.IGNORECASE)

# WHERE 子句中出现的 user_id = 字面量须与当前会话用户一致（防模型代查他人）
_USER_ID_EQ_PATTERN = re.compile(
    r"\buser_id\s*=\s*(?:'([^']*)'|\"([^\"]*)\"|(\d+))",
    re.IGNORECASE,
)


def _extract_table_name(sql: str) -> str | None:
    m = FROM_TABLE_PATTERN.search(sql)
    if not m:
        return None
    name = (m.group(1) or m.group(2) or "").strip()
    return name.lower() if name else None


def _extract_referenced_tables(sql: str) -> set[str]:
    def _normalize_table_token(raw: str) -> str:
        # 兼容 schema.table / `schema`.`table` / table 三种写法，只保留实际表名部分。
        token = str(raw or "").strip().strip("`").lower()
        if "." in token:
            token = token.split(".")[-1].strip("`")
        return token

    tables: set[str] = set()
    for m in FROM_TABLE_PATTERN.finditer(sql or ""):
        name = _normalize_table_token(m.group(1) or m.group(2) or "")
        if name:
            tables.add(name)
    for m in JOIN_TABLE_PATTERN.finditer(sql or ""):
        name = _normalize_table_token(m.group(1) or m.group(2) or "")
        if name:
            tables.add(name)
    return tables


def _validate_user_id_literals_match_session(sql: str, user_id: str) -> None:
    """若 SQL 中显式写了 user_id = 字面量，必须与当前会话 user_id 一致。"""
    uid = str(user_id).strip()
    for m in _USER_ID_EQ_PATTERN.finditer(sql):
        raw = m.group(1) if m.group(1) is not None else (m.group(2) if m.group(2) is not None else m.group(3))
        if raw is None:
            continue
        if str(raw).strip() != uid:
            raise ValueError(f"user_scope_literal_mismatch:user_id={raw}")


def _qualify_owner_column(sql: str, table_name: str, owner_col: str) -> str:
    """单表可写裸列名；含 JOIN 时必须带表名/别名，否则 MySQL 报列歧义。"""
    if not re.search(r"\bjoin\b", sql, re.IGNORECASE):
        return owner_col
    # FROM orders o JOIN / FROM `orders` AS `o` JOIN（别名可能带反引号）
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
    # FROM orders JOIN ...（无别名）
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
    """GROUP BY / HAVING / ORDER BY / LIMIT 中最早出现的位置；无则 len(sql)。"""
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


def _enforce_user_scope(sql: str, table_name: str, user_id: str) -> tuple[str, list[Any]]:
    spec = SHOP_TABLE_CATALOG.get(table_name)
    if spec is None:
        raise ValueError(f"table_not_allowed:{table_name}")

    if not SELECT_PATTERN.search(sql):
        raise ValueError("only_select_allowed")

    if spec.owner_column is None:
        return sql, []

    owner_col = _qualify_owner_column(sql, table_name, spec.owner_column)
    insert_at = _first_clause_after_select(sql)
    has_where = re.search(r"\bwhere\b", sql, re.IGNORECASE) is not None

    # 已有 WHERE：必须把 AND owner=... 插在 WHERE 条件之后、ORDER/LIMIT 等之前，
    # 绝不能接在整个 SQL 末尾（否则会生成「LIMIT 5 AND user_id=%s」非法语法）。
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
    """去掉首尾空白与末尾分号；模型常输出 `... LIMIT 5;` 单条语句，不应与多语句混为一谈。"""
    s = sql.strip()
    while s.endswith(";"):
        s = s[:-1].rstrip()
    return s


def execute_user_scoped_sql(sql: str, user_id: str) -> list[dict[str, Any]]:
    settings = load_settings()
    sql = _normalize_sql_statement(sql)
    if not sql:
        raise ValueError("empty_sql")
    table = _extract_table_name(sql)
    if not table:
        raise ValueError("missing_from_table")
    if table not in SHOP_TABLE_CATALOG:
        raise ValueError(f"table_not_allowed:{table}")
    if ";" in sql:
        raise ValueError("multi_statement_not_allowed")
    _validate_user_id_literals_match_session(sql, user_id)

    scoped_sql, params = _enforce_user_scope(sql, table, user_id)
    scoped_sql = _append_limit(scoped_sql, settings.sql_max_rows)

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
