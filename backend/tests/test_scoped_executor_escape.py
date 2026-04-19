"""scoped_executor：PyMySQL % 转义、无引号 user_id 修正。"""

from app.db_access.scoped_executor import (
    _escape_percent_for_pymysql,
    _fix_bare_session_user_equals,
)


def test_escape_percent_preserves_param_placeholder() -> None:
    s = "SELECT * FROM products WHERE name LIKE '%米%' AND id = %s"
    out = _escape_percent_for_pymysql(s)
    assert "%s" in out
    assert "%%" in out
    assert "米" in out


def test_fix_bare_user_id() -> None:
    raw = "SELECT * FROM orders WHERE user_id = demo_user"
    out = _fix_bare_session_user_equals(raw, "demo_user")
    assert "user_id = 'demo_user'" in out
    assert " = demo_user" not in out


def test_fix_bare_with_alias() -> None:
    raw = "SELECT 1 FROM orders o WHERE o.user_id = demo_x"
    out = _fix_bare_session_user_equals(raw, "demo_x")
    assert "o.user_id = 'demo_x'" in out
