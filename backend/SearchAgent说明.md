# SearchAgent 说明（函数与调用关系）

本文档基于 [`app/agents/search_agent.py`](app/agents/search_agent.py) 与 [`app/agents/query_display.py`](app/agents/query_display.py)。

## 1. 职责概览

`SearchAgent` 负责 **query 意图**：用 LangChain **Text-to-SQL 链**生成 **单条 SELECT**，本地做 **格式校验**（`SELECT`、须有 `FROM`、禁止分号；**允许 JOIN**）与 **范围执行**（[`execute_user_scoped_sql`](app/tools/sql_query_tool.py)：白名单表、按 `user_id` 参数化作用域、JOIN 时对主表 `user_id` 自动加表/别名限定、可选校验 `WHERE` 中 `user_id` 字面量）。

**展示与列映射**、**从结果行推断 `task_context.outputs`**（`proposed_order_items`、`unpaid_order_ids` 等）均在 [`query_display.py`](app/agents/query_display.py)，不再做订单行补全或数据库比价。

---

## 2. 模块 `query_display.py`

| 名称 | 作用 |
|------|------|
| `COLUMN_LABEL_ZH` / `ORDER_STATUS_ZH` / `UNPAID_ORDER_STATUSES` | 展示用常量 |
| `format_cell` / `format_row_for_display` / `format_line_items_brief` | 单元格与行展示 |
| `unpaid_order_ids_from_rows` | 从未支付订单行提取 id |
| `build_query_result_template` | 「查询结果：」模板 |
| `build_citation_snippets` | 引用片段列表 |
| `build_search_task_outputs` | 从主查询行推断 `outputs`（无比价） |

---

## 3. 类 `SearchAgent`

| 方法 | 说明 |
|------|------|
| `__init__` | `load_settings`、`LLMRouter(search_agent_llm)`、`catalog_prompt_text()`、`_build_sql_chain()` |
| `_build_sql_chain` | `SQLDatabase` + `create_sql_query_chain` |
| `_extract_sql_from_chain_output` | 从链输出解析 SQL 字符串 |
| `_invoke_sql_chain` | 线程池超时包装 `sql_chain.invoke` |
| `_generate_sql` | 拼接 prompt（两条业务约束 + 技术约束），调用链 |
| `handle(text, user_id)` | 生成 SQL → 校验 → `execute_user_scoped_sql` → `query_display` 组装结果；返回 `(AgentResult, {}, unpaid_ids, row_dicts)` |
| `handle_with_state(state, text)` | 调 `handle`；写 `sql_query_trace`；`task_context[task_id]["outputs"] = build_search_task_outputs(row_dicts)` |

---

## 4. 编排器入口

[`app/core/orchestrator.py`](app/core/orchestrator.py)：`MultiAgentOrchestrator._query_node` → `search_agent.handle_with_state(state, task.text)`。

---

## 5. `sql_query_tool` 与 SearchAgent 的衔接

| 函数 | 用途 |
|------|------|
| `is_valid_select_with_from` | 解析 `FROM`（支持反引号表名） |
| `execute_user_scoped_sql` | 校验表名目录、允许多表 JOIN、禁止多语句、`user_id` 字面量、`_enforce_user_scope` 后执行 |

已移除：`fetch_order_line_items_with_products`、`execute_scoped_sql_with_order_line_items`、`compute_drop_items_from_order_line_items`。

---

## 6. 数据流（简图）

```mermaid
flowchart LR
  U[UserText]
  Chain[sql_chain_invoke]
  V[format_and_join_checks]
  E[execute_user_scoped_sql]
  D[query_display]
  U --> Chain --> V --> E --> D
```
