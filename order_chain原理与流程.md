# Order Chain 原理与流程说明

## 1. 文档目的
本文档说明 `backend/app/chains/order_chain.py` 的实现原理、状态机流程、工具调用方式和异常兜底逻辑，便于后续维护和审计。

## 2. 核心定位
`OrderChain` 是订单场景的强控制执行链，负责处理三类订单操作：
- `create`（下单）
- `cancel`（退单）
- `modify`（修改订单）

它的关键目标是：先收集信息，再执行前确认，执行后等待用户点击确认，最后闭环结束。

## 3. 关键数据结构与状态

### 3.1 OrderContext（跨轮状态）
由外部维护并传入 `OrderChain`，核心字段：
- `operation`：订单动作类型
- `status`：流程状态
- `fields`：已收集字段
- `order_link`：执行结果链接
- `failure_reason`：失败原因

### 3.2 状态枚举（业务语义）
- `collecting_info`：信息收集中
- `awaiting_pre_confirm`：执行前确认
- `executed_waiting_click`：已执行，等待用户点击确认
- `closed`：流程结束
- `failed`：执行失败

## 4. Chain 结构（LangChain Runnable）

`process_user_text()` 中定义了线性步骤链：

1. `_step_route_or_operation`
2. `_step_handle_confirm_stage`
3. `_step_block_if_executed`
4. `_step_collect_and_validate`
5. `_step_prepare_pre_confirm`

每一步都处理 `payload`，通过 `done=True/False` 控制是否短路。  
只要某一步返回 `done=True`，后续步骤不再继续业务推进。

## 5. 主流程说明

### 5.1 入口：process_user_text(ctx, text)
- 输入：当前会话 `OrderContext` 和用户文本
- 行为：执行上述步骤链
- 输出：`AgentResult`

### 5.2 步骤 1：识别操作类型
- 若 `ctx.operation` 为空，调用 `_detect_operation(text)` 做关键词识别
- 无法识别时直接返回：
  - `action_required=provide_order_operation`
  - 引导用户说明是下单/退单/修改

### 5.3 步骤 2：处理“执行前确认”阶段
- 仅当 `ctx.status == awaiting_pre_confirm` 时生效
- 用户输入是确认/取消时，进入 `apply_pre_confirm()`
- 输入不是明确确认词时，返回：
  - `action_required=confirm_before_execute`
  - 提示必须回复“确认”或“取消”

### 5.4 步骤 3：执行后阻断
- 若 `ctx.status == executed_waiting_click`，直接返回提醒：
  - `action_required=click_order_link_confirm`
  - 防止重复执行工具

### 5.5 步骤 4：字段提取与校验
- `_extract_fields()` 使用正则从文本提取字段
- `_missing_fields()` 按 `REQUIRED_FIELDS` 校验
- 缺字段时返回：
  - `status=collecting_info`
  - `action_required=provide_order_fields`

### 5.6 步骤 5：推进到执行前确认
- 当字段齐全且未短路时，设为：
  - `ctx.status = awaiting_pre_confirm`
- 返回确认提示，等待用户明确授权执行

## 6. 执行与收尾

### 6.1 apply_pre_confirm(ctx, confirm)
- 前置条件：`ctx.status == awaiting_pre_confirm`
- `confirm=False`：直接关闭流程（`closed`）
- `confirm=True`：调用 `execute(ctx)`

### 6.2 execute(ctx)
- 根据 `operation` 调工具函数：
  - `create_order(ctx.fields)`
  - `cancel_order(ctx.fields)`
  - `modify_order(ctx.fields)`
- 失败：
  - `ctx.status = failed`
  - 记录 `failure_reason`
- 成功：
  - `ctx.status = executed_waiting_click`
  - 写入 `order_link`
  - 返回 `action_required=click_order_link_confirm`

### 6.3 finalize(ctx, clicked)
- 仅在 `executed_waiting_click` 状态可确认结束
- `clicked=False`：继续提示点击链接确认
- `clicked=True`：流程关闭（`closed`）

## 7. 字段规则

`REQUIRED_FIELDS` 约束：
- `create`：`item_name`, `quantity`, `address`, `contact_phone`
- `cancel`：`order_id`, `reason`
- `modify`：`order_id`, `field`, `new_value`

字段提取基于中文/英文关键词正则（如 `订单号/order_id`、`地址/address`、`原因/reason`）。

## 8. 失败与兜底策略
- 未识别操作：要求补充操作类型
- 未补齐字段：要求补字段，不可跳步
- 确认阶段输入不明确：要求“确认/取消”二选一
- 工具执行失败：进入 `failed`，返回失败原因
- 执行后未点击确认：阻断后续重复执行，仅提示收尾动作

## 9. 与系统图编排关系
- 图层路由到 `order_agent` 后，`order_agent` 调 `OrderChain`
- `OrderChain` 只负责订单流程内部强控制
- 外层 `orchestrator` 负责会话级编排、状态落库和跨任务汇总

## 10. 总结
`OrderChain` 本质是“可短路的步骤链 + 显式状态机 + 工具执行闸门”：
- 保证订单执行前必须确认
- 保证执行后必须点击确认再闭环
- 保证每个失败点都有可解释反馈
