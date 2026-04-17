import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any
from urllib.parse import quote_plus

from langchain_classic.chains.sql_database.query import create_sql_query_chain
from langchain_community.utilities import SQLDatabase

from app.agents.query_display import (
    build_citation_snippets,
    build_query_result_template,
    build_search_task_outputs,
)
from app.core.state import AgentResult, SqlQueryTaskRecord
from app.core.llm_provider import LLMRouter
from app.core.settings import load_settings
from app.tools.sql_catalog import catalog_prompt_text
from app.tools.sql_query_tool import execute_user_scoped_sql, is_valid_select_with_from


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
            "2）不得查询、暴露或筛选到其他用户的数据；仅与当前会话用户相关（当前用户标识见文末）。\n"
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
            "技术约束：单条 SELECT，允许 JOIN 多表关联；禁止子查询、多语句与分号；列与表须在目录内。\n"
            f"表结构：\n{self.table_catalog_prompt}\n"
            f"当前会话用户标识 user_id：{user_id}\n"
            f"用户问题：{text}\n"
            "只输出 SQL，不要解释。"
        )
        chain_out = self._invoke_sql_chain(question)
        raw = chain_out if isinstance(chain_out, str) else str(chain_out)
        return self._extract_sql_from_chain_output(raw)

    def _extract_order_ids_from_rows(self, rows: list[dict[str, Any]]) -> list[int]:
        out: list[int] = []
        seen: set[int] = set()
        for r in rows:
            d = dict(r)
            raw = d.get("order_id")
            if raw is None:
                raw = d.get("id")
            if raw is None:
                continue
            s = str(raw).strip()
            if not s.isdigit():
                continue
            oid = int(s)
            if oid in seen:
                continue
            seen.add(oid)
            out.append(oid)
        return out

    def _fetch_items_by_order_ids(self, order_ids: list[int], user_id: str) -> list[dict[str, Any]]:
        if not order_ids:
            return []
        ids_sql = ", ".join(str(i) for i in order_ids)
        sql = (
            "SELECT oi.order_id, oi.product_id, oi.quantity, oi.unit_price, p.name AS item_name "
            "FROM order_items oi "
            "LEFT JOIN products p ON p.id = oi.product_id "
            f"WHERE oi.order_id IN ({ids_sql})"
        )
        rows = execute_user_scoped_sql(sql, user_id)
        return [dict(r) for r in rows]

    def handle(
        self, text: str, user_id: str
    ) -> tuple[AgentResult, dict[str, list[dict[str, Any]]], list[str], list[dict[str, Any]]]:
        try:
            sql = self._generate_sql(text, user_id)
        except Exception as exc:
            err = str(exc)
            if err == "sql_chain_unavailable":
                msg = "大模型未配置或不可用，无法生成查询。"
                code = "sql_chain_unavailable"
            elif isinstance(exc, TimeoutError) or "timeout" in err.lower():
                msg = "大模型生成 SQL 超时，请稍后重试或调大环境变量 LLM_INVOKE_TIMEOUT_S。"
                code = err
            else:
                msg = "大模型生成 SQL 失败，请稍后重试或换一种问法。"
                code = err
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
                    message="大模型生成的 SQL 不可用，无法执行。",
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
            err = str(exc)
            if err == "order_query_requires_orders_and_order_items":
                msg = "订单查询需同时联查 orders 与 order_items，请重试。"
            elif err.startswith("table_not_allowed:"):
                msg = "查询引用了未授权表，请换一种问法。"
            elif err in {"missing_from_table", "only_select_allowed", "multi_statement_not_allowed"}:
                msg = "生成的 SQL 不符合执行约束，请重试。"
            else:
                msg = "查询执行失败，请稍后重试。"
            return (
                AgentResult(
                    route="query",
                    status="error",
                    message=msg,
                    error=err,
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
                    message="未查询到相关数据。",
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
        # 兜底：若主查询未携带商品明细但拿到了订单号，则补查 order_items+products，
        # 以保证后续订单修改/退单表单可回填商品名称与数量。
        if not outputs.get("proposed_order_items"):
            order_ids = self._extract_order_ids_from_rows(row_dicts)
            if order_ids:
                try:
                    item_rows = self._fetch_items_by_order_ids(order_ids, user_id)
                except Exception:
                    item_rows = []
                if item_rows:
                    proposed: list[dict[str, Any]] = []
                    seen: set[tuple[str, str]] = set()
                    for r in item_rows:
                        name = str(r.get("item_name") or "").strip()
                        qty = str(r.get("quantity") or "").strip() or "1"
                        if not name:
                            continue
                        key = (name, qty)
                        if key in seen:
                            continue
                        seen.add(key)
                        item: dict[str, Any] = {"item_name": name, "quantity": qty}
                        pid = r.get("product_id")
                        if pid is not None:
                            item["product_id"] = pid
                        proposed.append(item)
                    if proposed:
                        outputs["proposed_order_items"] = proposed
                        by_oid: dict[str, list[dict[str, Any]]] = dict(
                            outputs.get("order_items_by_order_id") or {}
                        )
                        for r in item_rows:
                            rd = dict(r)
                            oid_s = str(rd.get("order_id") or "").strip()
                            if not oid_s:
                                continue
                            name = str(rd.get("item_name") or "").strip()
                            qty = str(rd.get("quantity") or "").strip() or "1"
                            if not name:
                                continue
                            line: dict[str, Any] = {"item_name": name, "quantity": qty}
                            pid = rd.get("product_id")
                            if pid is not None:
                                line["product_id"] = pid
                            bucket = by_oid.setdefault(oid_s, [])
                            sig = (line.get("product_id"), line["item_name"], line["quantity"])
                            if not any(
                                (x.get("product_id"), x.get("item_name"), str(x.get("quantity"))) == sig
                                for x in bucket
                            ):
                                bucket.append(line)
                        outputs["order_items_by_order_id"] = by_oid
        task_ctx = runtime.setdefault("task_context", {})
        tctx = task_ctx.setdefault(task_id, {})
        tctx["outputs"] = outputs
        return {"runtime": {**runtime, "raw": result}}
