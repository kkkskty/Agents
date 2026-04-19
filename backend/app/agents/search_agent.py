from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any
from urllib.parse import quote_plus

from langchain_classic.chains.sql_database.query import create_sql_query_chain
from langchain_community.utilities import SQLDatabase

from app.db_access import (
    build_citation_snippets,
    build_query_result_template,
    build_search_task_outputs,
    catalog_prompt_text,
    execute_user_scoped_sql,
    is_valid_select_with_from,
)
from app.core.state import AgentResult, SqlQueryTaskRecord
from app.core.llm_provider import LLMRouter
from app.core.settings import load_settings


class SearchAgent:
    def __init__(self) -> None:
        self.settings = load_settings()
        self.llm = LLMRouter(self.settings.search_agent_llm)
        self.table_catalog_prompt = catalog_prompt_text()
        self.sql_chain = self._build_sql_chain()

    def _build_sql_chain(self):
        llm = self.llm.get_llm()
        if llm is None:
            return None
        password = quote_plus(self.settings.mysql_password)
        db_uri = (
            f"mysql+pymysql://{self.settings.mysql_user}:{password}"
            f"@{self.settings.mysql_host}:{self.settings.mysql_port}/{self.settings.mysql_database}"
        )
        db = SQLDatabase.from_uri(db_uri)
        return create_sql_query_chain(llm=llm, db=db)

    def _extract_sql_from_chain_output(self, output: str) -> str:
        text = output.strip()
        if "SQLQuery:" in text:
            text = text.split("SQLQuery:", 1)[1].strip()
        if "```" in text:
            text = text.replace("```sql", "").replace("```", "").strip()
        lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("--")]
        if not lines:
            return text
        if lines[0].lower().startswith("select"):
            joined = " ".join(lines)
            if is_valid_select_with_from(joined):
                return joined
        if "\n" in text:
            first = text.splitlines()[0].strip()
            if first.lower().startswith("select") and is_valid_select_with_from(first):
                return first
        return " ".join(lines) if len(lines) > 1 else lines[0]

    def _invoke_sql_chain(self, question: str) -> Any:
        assert self.sql_chain is not None
        timeout_s = float(self.settings.search_agent_llm.llm_invoke_timeout_s)
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(self.sql_chain.invoke, {"question": question})
            try:
                return fut.result(timeout=timeout_s)
            except FutureTimeoutError as exc:
                raise TimeoutError(
                    f"sql_chain invoke timeout after {timeout_s:.1f}s (LLM_INVOKE_TIMEOUT_S)"
                ) from exc

    def _generate_sql(self, text: str, user_id: str) -> str:
        if self.sql_chain is None:
            raise RuntimeError("sql_chain_unavailable")
        question = (
            "你是 Text-to-SQL 助手。根据用户问题生成**一条** MySQL SELECT 语句。\n"
            "业务约束（必须遵守）：\n"
            "1）只能查询下面「表结构」中出现的表，禁止引用未提供的表；\n"
            "2）不得查询、暴露或筛选到其他用户的数据。"
            "users/orders/order_items/refunds 须按 user_id 限定；products 为全站商品目录（无 user_id 列），"
            "仅查商品名/价/库存时只使用 products，禁止对 products 写 user_id 条件或与 orders 无关的硬联表。\n"
            "3）只生成用户语义的sql，不要生成其他无关的sql。\n"
            "4）比价语义（生成 WHERE 条件时必须一致）：「成交单价」指 order_items.unit_price，「当前标价」指 products.price；"
            "若「当前标价」<「成交单价」，表示现价比下单时更便宜（降价/可捡漏重下单）；若「当前标价」> 「成交单价」，表示现价比下单时更贵（涨价）。\n"
            "5）凡是订单查询（涉及 orders 或 order_items）必须同时联查 orders 与 order_items，不允许只查其中一张。\n"
            "6）涉及“降价/比价/重新下单”时，优先使用固定联表骨架："
            "FROM orders o JOIN order_items oi ON oi.order_id=o.id "
            "JOIN products p ON p.id=oi.product_id。\n"
            "7）当用户查询“未付款/待支付订单”（尤其带有“修改/退单/继续处理订单”语义）时，"
            "必须使用 orders o JOIN order_items oi ON oi.order_id=o.id，"
            "并在 SELECT 中包含：o.id AS order_id、o.status、o.total_amount、o.created_at、"
            "oi.product_id、oi.quantity、oi.unit_price（可按语义增减其他列）。\n"
            "8）「仅商品价格 / 库存 / 搜商品名」且未提及订单、订单号、「我的订单」时：仅用单表 products；"
            "示例：SELECT id, name, price, stock FROM products WHERE name LIKE '%关键词%'；"
            "关键词取自用户问题（如小米充电器）；不要 JOIN orders/order_items；不要用子查询；"
            "products 表无 user_id 列，禁止出现 products.user_id。\n"
            "9）凡在 WHERE/JOIN 中写 users.id、orders.user_id、order_items.user_id 等与用户相关的等值条件时，"
            "必须与下方「当前会话用户标识」完全一致：若标识为数字请用同一数字（如 1）；若标识含字母请用带引号的字符串（如 'demo_user'）。"
            "禁止写成无引号的裸词（如 user_id = demo_user），否则会被数据库当成列名而报错。\n"
            "10）禁止使用窗口函数（含 SUM(...)、AVG(...) 等与 OVER (...) 连用，以及 ROW_NUMBER() OVER 等）；"
            "MySQL 易报 3593。筛选「订单里已降价商品」请用 JOIN products 后直接比较列："
            "WHERE p.price < oi.unit_price（当前标价低于成交单价即相对下单时更便宜）；"
            "不要用 SUM(...) OVER、不要在 WHERE 里写窗口表达式。\n"
            "技术约束：单条 SELECT，允许 JOIN 多表关联；禁止子查询、多语句与分号；列与表须在目录内。\n"
            f"表结构：\n{self.table_catalog_prompt}\n"
            f"当前会话用户标识 user_id：{user_id}\n"
            f"用户问题：{text}\n"
            "只输出 SQL，不要解释。"
        )
        chain_out = self._invoke_sql_chain(question)
        raw = chain_out if isinstance(chain_out, str) else str(chain_out)
        return self._extract_sql_from_chain_output(raw)

    def handle(
        self, text: str, user_id: str
    ) -> tuple[AgentResult, dict[str, list[dict[str, Any]]], list[str], list[dict[str, Any]]]:
        try:
            sql = self._generate_sql(text, user_id)
        except Exception as exc:
            err = repr(exc)
            simple = str(exc)
            if simple == "sql_chain_unavailable":
                msg = "sql_chain_unavailable：大模型未配置或不可用。"
                code = "sql_chain_unavailable"
            elif isinstance(exc, TimeoutError) or "timeout" in err.lower():
                msg = f"生成 SQL 超时：{err}"
                code = simple or err
            else:
                msg = f"生成 SQL 失败：{err}"
                code = simple or err
            return (
                AgentResult(
                    route="query",
                    status="error",
                    message=msg,
                    error=code,
                    sql_query=None,
                ),
                {},
                [],
                [],
            )

        if not is_valid_select_with_from(sql):
            return (
                AgentResult(
                    route="query",
                    status="error",
                    message=f"生成的 SQL 不可用（校验未通过）：{sql!r}",
                    error="invalid_generated_sql",
                    sql_query=sql,
                ),
                {},
                [],
                [],
            )

        try:
            rows = execute_user_scoped_sql(sql, user_id)
        except Exception as exc:
            err = repr(exc)
            simple = str(exc)
            if simple == "order_query_requires_orders_and_order_items":
                msg = f"订单查询需同时联查 orders 与 order_items。{err}"
            elif simple.startswith("table_not_allowed:"):
                msg = f"引用了未授权表。{err}"
            elif simple.startswith("user_scope_literal_mismatch:"):
                msg = f"用户范围与当前登录不一致。{err}"
            elif simple == "window_function_not_allowed":
                msg = (
                    "生成的 SQL 使用了窗口函数（如 SUM OVER），当前库不支持该写法。"
                    "比价/降价请使用：FROM orders o JOIN order_items oi … JOIN products p … "
                    "且 WHERE p.price < oi.unit_price（并限定用户订单）。"
                )
            elif simple in {"missing_from_table", "only_select_allowed", "multi_statement_not_allowed"}:
                msg = f"SQL 不满足执行约束。{err}"
            else:
                msg = f"SQL 执行失败：{err}"
            return (
                AgentResult(
                    route="query",
                    status="error",
                    message=msg,
                    error=simple or err,
                    sql_query=sql,
                ),
                {},
                [],
                [],
            )

        row_dicts = [dict(r) for r in rows]
        if not row_dicts:
            return (
                AgentResult(
                    route="query",
                    status="no_result",
                    message=f"查询无数据（SQL 已执行）：{sql!r}",
                    error="search_no_result",
                    sql_query=sql,
                ),
                {},
                [],
                [],
            )

        template = build_query_result_template(row_dicts)
        citation_snippets = build_citation_snippets(row_dicts)
        return (
            AgentResult(
                route="query",
                status="ok",
                message=template,
                sql_query=sql,
                citations=[
                    {"source": "mysql:shop", "chunk_id": idx + 1, "snippet": snip}
                    for idx, snip in enumerate(citation_snippets)
                ],
            ),
            {},
            build_search_task_outputs(row_dicts)["unpaid_order_ids"],
            row_dicts,
        )

    def handle_with_state(self, state, text: str) -> dict:
        runtime = state["runtime"]
        trace_state = state["trace"]
        user_id = state["session"]["conversation"].user_id
        result, _line_items, _unpaid_ids, row_dicts = self.handle(text, user_id)
        idx = runtime["current_task_index"]
        tasks = runtime["sub_tasks"]
        task_id = tasks[idx].id if 0 <= idx < len(tasks) else "unknown"
        trace = trace_state["sql_query_trace"]
        trace.records.append(
            SqlQueryTaskRecord(
                task_id=task_id,
                last_sql=result.sql_query,
                citations=list(result.citations) if result.citations else [],
                order_line_items_by_order_id={},
                status=result.status,
                error=result.error,
            )
        )
        outputs = build_search_task_outputs(row_dicts)
        task_ctx = runtime.setdefault("task_context", {})
        tctx = task_ctx.setdefault(task_id, {})
        tctx["outputs"] = outputs
        return {"runtime": {**runtime, "raw": result}}
