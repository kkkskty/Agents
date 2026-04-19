"""查询结果的可读格式化：单元格、行、模板、引用；订单状态等与目录解耦。"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.db_access.catalog import TableCatalog, get_default_catalog, label_for_column

# 枚举类状态值展示（非列名）；可按需改为配置
ORDER_STATUS_DISPLAY: dict[str, str] = {
    "pending": "待处理",
    "completed": "已完成",
    "cancelled": "已取消",
    "paid": "已支付",
    "shipped": "已发货",
    "refunded": "已退款",
    "failed": "失败",
}


def format_cell(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, Decimal):
        s = format(value, "f")
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return s
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return str(value)


def format_row_for_display(
    row: dict[str, Any],
    catalog: TableCatalog | None = None,
) -> str:
    cat = catalog or get_default_catalog()
    parts: list[str] = []
    for key, raw in row.items():
        label = label_for_column(str(key), cat)
        if key == "status" and isinstance(raw, str):
            v_zh = ORDER_STATUS_DISPLAY.get(raw.strip().lower(), format_cell(raw))
            parts.append(f"{label}：{v_zh}")
        elif key == "total_amount":
            parts.append(f"{label}：{format_cell(raw)} 元")
        else:
            parts.append(f"{label}：{format_cell(raw)}")
    return "，".join(parts)


def format_line_items_brief(items: list[dict[str, Any]]) -> str:
    if not items:
        return "（无明细）"
    segs: list[str] = []
    for it in items:
        name = str(it.get("product_name") or "商品")
        qty = it.get("quantity", 0)
        price = it.get("unit_price")
        price_s = ""
        if price is not None:
            price_s = f"，单价 {format_cell(price)} 元"
        segs.append(f"{name} ×{qty}{price_s}")
    return "；".join(segs)


def build_query_result_template(
    rows: list[dict[str, Any]],
    max_display: int = 3,
    catalog: TableCatalog | None = None,
) -> str:
    cat = catalog or get_default_catalog()
    display_lines: list[str] = []
    for r in rows[:5]:
        display_lines.append(format_row_for_display(dict(r), cat))
    snippets = "\n".join(f"{i}. {line}" for i, line in enumerate(display_lines[:max_display], start=1))
    template = f"查询结果：\n{snippets}"
    if len(display_lines) > max_display:
        template += f"\n（共 {len(rows)} 条，上文展示前 {max_display} 条）"
    elif len(rows) > len(display_lines):
        template += f"\n（共 {len(rows)} 条）"
    return template


def build_citation_snippets(
    rows: list[dict[str, Any]],
    limit: int = 5,
    catalog: TableCatalog | None = None,
) -> list[str]:
    cat = catalog or get_default_catalog()
    return [format_row_for_display(dict(r), cat) for r in rows[:limit]]
