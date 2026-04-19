"""Text-to-SQL 可调用的表目录、白名单与列展示名索引（与具体业务库名解耦）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class TableSpec:
    """单表元数据：列、属主列、LLM 用列说明、可选短中文展示名（与 columns 等长）。"""

    name: str
    columns: tuple[str, ...]
    owner_column: str | None = "user_id"
    description: str = ""
    column_descriptions: tuple[str, ...] = ()
    #: 与 columns 等长；空串表示展示时回退为列名英文；全空元组表示由全局 overrides 决定
    column_display: tuple[str, ...] = ()


@runtime_checkable
class TableCatalog(Protocol):
    """受控 SELECT 允许的表集合。"""

    def get(self, table: str) -> TableSpec | None:
        ...

    def allowed_names(self) -> frozenset[str]:
        ...

    def iter_specs(self) -> tuple[TableSpec, ...]:
        ...


@dataclass
class DictTableCatalog:
    """默认内存目录，可替换为测试假实现或日后 YAML 加载结果。"""

    _by_name: dict[str, TableSpec]

    def __post_init__(self) -> None:
        self._by_name = {k.lower(): v for k, v in self._by_name.items()}

    def get(self, table: str) -> TableSpec | None:
        return self._by_name.get((table or "").strip().lower())

    def allowed_names(self) -> frozenset[str]:
        return frozenset(self._by_name.keys())

    def iter_specs(self) -> tuple[TableSpec, ...]:
        return tuple(self._by_name.values())


def _format_columns_with_desc(t: TableSpec) -> str:
    if len(t.column_descriptions) == len(t.columns):
        parts: list[str] = []
        for col, desc in zip(t.columns, t.column_descriptions, strict=True):
            parts.append(f"{col}({desc})" if desc else col)
        return ", ".join(parts)
    return ", ".join(t.columns)


DEFAULT_TABLE_CATALOG: dict[str, TableSpec] = {
    "users": TableSpec(
        name="users",
        columns=("id", "username", "phone", "email", "created_at"),
        owner_column="id",
        description="用户基础信息表",
        column_descriptions=("主键", "登录名", "手机号", "邮箱", "注册时间"),
        column_display=("编号", "用户名", "手机", "邮箱", "创建时间"),
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
        column_display=("编号", "用户", "状态", "金额", "创建时间", "更新时间"),
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
        column_display=("编号", "订单号", "用户", "商品编号", "数量", "单价"),
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
        column_display=("编号", "名称", "分类", "价格", "库存", "更新时间"),
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
        column_display=("编号", "订单号", "用户", "状态", "原因", "创建时间"),
    ),
}

_default_catalog_singleton: DictTableCatalog | None = None


def get_default_catalog() -> DictTableCatalog:
    global _default_catalog_singleton
    if _default_catalog_singleton is None:
        _default_catalog_singleton = DictTableCatalog(dict(DEFAULT_TABLE_CATALOG))
    return _default_catalog_singleton


def catalog_prompt_text(catalog: TableCatalog | None = None) -> str:
    cat = catalog or get_default_catalog()
    lines: list[str] = []
    for t in cat.iter_specs():
        cols = _format_columns_with_desc(t)
        lines.append(
            f"- {t.name}({cols})"
            + (f", owner={t.owner_column}" if t.owner_column else ", owner=none")
            + f", desc={t.description}"
        )
    return "\n".join(lines)


def build_column_display_index(catalog: TableCatalog | None = None) -> dict[str, str]:
    """按注册表顺序，同名列后者覆盖前者，与列上 `column_display` 一致；空串则用列名。"""
    cat = catalog or get_default_catalog()
    out: dict[str, str] = {}
    for t in cat.iter_specs():
        displays = t.column_display
        for i, col in enumerate(t.columns):
            if i < len(displays) and displays[i]:
                out[col] = displays[i]
            else:
                out[col] = col
    return out


# 目录未声明的别名列（如 JOIN 别名、AS name）的展示回退
DEFAULT_LABEL_OVERRIDES: dict[str, str] = {
    "item_name": "商品",
    "product_name": "商品",
    "field": "字段",
    "new_value": "新值",
}


def label_for_column(key: str, catalog: TableCatalog | None = None) -> str:
    idx = build_column_display_index(catalog)
    k = str(key or "")
    if k in idx:
        return idx[k]
    return DEFAULT_LABEL_OVERRIDES.get(k, k)
