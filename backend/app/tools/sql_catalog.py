from dataclasses import dataclass


@dataclass(frozen=True)
class TableSpec:
    name: str
    columns: tuple[str, ...]
    owner_column: str | None = "user_id"
    description: str = ""
    """与 columns 等长；空串表示该列无额外说明。"""
    column_descriptions: tuple[str, ...] = ()


SHOP_TABLE_CATALOG: dict[str, TableSpec] = {
    "users": TableSpec(
        name="users",
        columns=("id", "username", "phone", "email", "created_at"),
        owner_column="id",
        description="用户基础信息表",
        column_descriptions=(
            "主键",
            "登录名",
            "手机号",
            "邮箱",
            "注册时间",
        ),
    ),
    "orders": TableSpec(
        name="orders",
        columns=("id", "user_id", "status", "total_amount", "created_at", "updated_at"),
        owner_column="user_id",
        description="订单主表",
        column_descriptions=(
            "订单主键",
            "所属用户 id",
            "订单状态：如 pending/unpaid 待支付，completed 已完成，cancelled 已取消，paid/shipped 等",
            "订单总金额",
            "创建时间",
            "最后更新时间",
        ),
    ),
    "order_items": TableSpec(
        name="order_items",
        columns=("id", "order_id", "user_id", "product_id", "quantity", "unit_price"),
        owner_column="user_id",
        description="订单商品明细",
        column_descriptions=(
            "明细行主键",
            "所属订单 id",
            "冗余的用户 id，便于按用户筛选",
            "商品 id，关联 products.id",
            "购买数量",
            "成交单价（下单时的单价）",
        ),
    ),
    "products": TableSpec(
        name="products",
        columns=("id", "name", "category", "price", "stock", "updated_at"),
        owner_column=None,
        description="商品信息表（公共数据）",
        column_descriptions=(
            "商品主键",
            "商品名称",
            "分类",
            "当前标价（可与 order_items.unit_price 比对价差）",
            "库存",
            "商品信息更新时间",
        ),
    ),
    "refunds": TableSpec(
        name="refunds",
        columns=("id", "order_id", "user_id", "status", "reason", "created_at"),
        owner_column="user_id",
        description="退款/退单记录",
        column_descriptions=(
            "记录主键",
            "关联订单 id",
            "申请人用户 id",
            "退款/退单处理状态",
            "原因说明",
            "申请或创建时间",
        ),
    ),
}


def _format_columns_with_desc(t: TableSpec) -> str:
    if len(t.column_descriptions) == len(t.columns):
        parts = []
        for col, desc in zip(t.columns, t.column_descriptions, strict=True):
            if desc:
                parts.append(f"{col}({desc})")
            else:
                parts.append(col)
        return ", ".join(parts)
    return ", ".join(t.columns)


def catalog_prompt_text() -> str:
    lines: list[str] = []
    for t in SHOP_TABLE_CATALOG.values():
        cols = _format_columns_with_desc(t)
        lines.append(
            f"- {t.name}({cols})"
            + (f", owner={t.owner_column}" if t.owner_column else ", owner=none")
            + f", desc={t.description}"
        )
    return "\n".join(lines)
