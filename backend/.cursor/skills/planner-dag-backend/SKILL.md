---
name: planner-dag-backend
description: 构建并维护后端的“Planner + DAG”执行链路。适用于修改意图拆解、任务依赖、task_context.outputs 契约、query/rule/order 编排、下单确认门禁，以及基于引用证据的总结逻辑（backend/app）。
---
# Planner DAG 后端技能

## 目标

保持后端与“薄路由 + 强 Planner + DAG 执行 + 证据化总结”架构一致。

## 何时使用

当任务涉及以下内容时启用本技能：

- 复杂请求需要“先查后做”
- `depends_on` 任务依赖关系
- `task_context.outputs` 契约设计
- 下单/执行前确认门禁
- 基于 `citations` 的总结质量

## 架构约束

1. 路由保持“薄”：
   - 路由只做粗粒度意图与兜底。
   - Planner/拆解负责任务列表与依赖关系。
2. 调度按“依赖就绪”执行，不按固定索引硬跑。
3. 每个任务都要把结构化产物写入 `task_context[task_id]["outputs"]`。
4. Summarizer 不得编造事实：
   - 回答必须由 `citations` + 结构化 outputs 支撑。
5. 高风险动作（如下单执行）必须显式确认后再执行。

## 核心文件

- `app/agents/intent_router.py`
- `app/core/orchestrator.py`
- `app/core/state.py`
- `app/agents/search_agent.py`
- `app/tools/sql_query_tool.py`
- `app/chains/order_chain.py`
- `app/agents/summarizer_agent.py`

## TaskContext 必备契约

每个任务至少落以下字段：

- `task_id`, `intent`, `question`
- `status`, `error`
- `citations`
- `depends_on`
- `outputs`

查询任务的 `outputs`（可用时）应包含：

- `ordered_items`
- `current_prices`
- `drop_items`
- `proposed_order_items`

## 实施检查清单

执行时复制并跟踪：

Task Progress:
- [ ] 校验任务拆解与依赖结构是否合法
- [ ] 确认调度器按依赖就绪选择任务
- [ ] 确认每个任务都写标准化 task_context
- [ ] 将比价等确定性计算放在工具层（不要让 LLM 文本硬算）
- [ ] 下单前确认 gate 生效
- [ ] Summarizer 读取 outputs + citations 且不回显原始证据长文
- [ ] 对变更文件执行编译/lint 自检

## 验证命令

在 `backend/` 目录执行：

```powershell
python -m compileall app -q
```

建议端到端场景：

- 输入：`我想查询自己的订单，哪些产品降价了？降价的产品我想下单`
- 预期：
  - 存在任务依赖（`order` 依赖前序 query/compare）
  - 返回拟下单方案
  - 执行前要求明确确认
  - 证据通过 `citations` 返回，与正文分离

## 常见坑

- 把应在工具层做的确定性逻辑写进 Summarizer
- `route=all_done` 直接透传到 API 响应
- 把证据原文直接拼进用户正文
- 未经过确认 gate 就执行下单

