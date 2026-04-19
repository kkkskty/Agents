"""从 SQL 结果行映射到 task_context.outputs 的策略（可通过环境变量 / AppSettings 调整）。"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.settings import AppSettings


@dataclass(frozen=True)
class SqlRowOutputPolicy:
    unpaid_statuses: frozenset[str]
    order_id_keys: tuple[str, ...]
    product_name_keys: tuple[str, ...]
    max_proposed_items: int = 20


def policy_from_app_settings(settings: AppSettings) -> SqlRowOutputPolicy:
    return SqlRowOutputPolicy(
        unpaid_statuses=settings.sql_unpaid_statuses, #待支付的订单状态
        order_id_keys=settings.sql_row_order_id_keys, #订单id的列名
        product_name_keys=settings.sql_row_product_name_keys, #商品名称的列名
        max_proposed_items=settings.sql_max_proposed_order_items, #最大推荐的商品数量
    )
