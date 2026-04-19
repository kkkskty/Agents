"""sql_query_tool：禁止窗口函数 SQL，避免 MySQL 3593。"""

import pytest

from app.tools.sql_query_tool import validate_sql_before_execute


def test_validate_rejects_sum_over() -> None:
    sql = (
        "SELECT SUM(oi.unit_price) OVER (PARTITION BY o.id) AS x "
        "FROM orders o JOIN order_items oi ON oi.order_id=o.id"
    )
    with pytest.raises(ValueError, match="window_function_not_allowed"):
        validate_sql_before_execute(sql)


def test_validate_allows_plain_join_compare() -> None:
    sql = (
        "SELECT o.id FROM orders o JOIN order_items oi ON oi.order_id=o.id "
        "JOIN products p ON p.id=oi.product_id WHERE p.price < oi.unit_price"
    )
    validate_sql_before_execute(sql)
