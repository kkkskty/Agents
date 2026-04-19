# 开发与测试计划（developPaln）

> 本文档为可执行的**开发任务**与**测试活动**汇总，与 [README.md](./README.md)、[plan.md](./plan.md) 保持一致。若需规范文件名，可自行重命名为 `developPlan.md`。

---

## 1. 范围与目标

| 项 | 说明 |
|----|------|
| **产品** | 游戏社区玩家 Web 智能客服（抢购、发帖、社交、充值、举报等场景）。 |
| **已完成** | `frontend/`：登录、聊天、快捷问题、本地演示回复、`VITE_API_BASE_URL` 可选对接。 |
| **待完成** | Python 后端：配置、双 LLM、RAG（Chroma）、Agent（LangGraph）、基于本地 MySQL 的工具层（订单/优惠券/转人工）、FastAPI `/api/chat`、流式与硬化等。 |
| **成功标准** | README「验收标准」+ `plan.md` 各 Phase 检查清单全部满足；前后端可稳定联调。 |

---

## 2. 开发计划（按阶段）

### 2.1 Phase 0 — 基建与联调骨架

| 序号 | 任务 | 产出/备注 |
|------|------|-----------|
| D0.1 | 初始化 `pyproject.toml`、`src/` 同级模块结构（`settings.py`、`aicode_types.py`、`llm/`、`embeddings/`、`rag/`、`addrag/`）与 `assistant/` 兼容入口壳 | 可安装、可 import |
| D0.2 | `pydantic-settings` 加载 LLM 相关环境变量（`LLM_PROVIDER`、`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`、`OPENAI_TEMPERATURE` 等） | 无密钥入库 |
| D0.3 | LLM 抽象：OpenAI 兼容 + Ollama（或同类），统一 `chat()` | 单元测试可 mock HTTP |
| D0.4 | CLI：`assistant ask "..."` 走通双 provider | 与 plan 一致 |
| D0.5 | FastAPI 最小应用：`POST /api/chat`，body 与 [frontend/src/lib/chatApi.ts](./frontend/src/lib/chatApi.ts) 一致，返回 `{ "reply": "..." }` | 先固定占位文案亦可 |
| D0.6 | 配置 `CORSMiddleware`，允许本地 `frontend` 源（开发机 Vite 端口） | 与 `VITE_API_BASE_URL` 联调通过 |

**阶段完成定义**：CLI 双后端各成功一次；浏览器中前端指向后端无 CORS/404。

---

### 2.2 Phase 1 — RAG 闭环与 HTTP 接入

| 序号 | 任务 | 产出/备注 |
|------|------|-----------|
| D1.1 | Ingest：TXT/Markdown + PDF 基础解析，可配置分块；并提供 Phase1 demo 文档 fixture，由独立命令 `python -m addrag ingest-md`（md-only）写入 Chroma 后即可检索 | fixture 文档入库 |
| D1.2 | Embedding 管线 + **Chroma** 写入/读取，维度校验；云端 embeddings 不可用（如 404）时自动回退到本地哈希向量（保证 Phase1 demo 可跑） | 错维启动失败或明确报错 |
| D1.3 | 检索器 Top-K + 元数据（文件名、块 id） | 可单测 |
| D1.4 | Prompt 拼装：检索片段注入 + 生成回答 + **引用列表**（结构清晰，便于前端扩展） | 当前前端仅展示 `reply` 字符串，引用可先放在文本或 JSON 扩展字段（若改契约需同步 `chatApi`） |
| D1.5 | `/api/chat` 接入完整 RAG 对话链：对最后一条 `user` 做 Top-K 检索，注入 `system`（知识库检索片段），并在回复末尾追加 `【引用】`兜底引用；支持多轮 `messages` | 与前端历史一致；若云端 `/embeddings` 不可用，会自动回退到本地哈希向量进行 Phase1 demo |

**阶段完成定义**：ingest 后针对 fixture 提问，回答含可识别引用；前端或 curl 端到端成功。

---

### 2.3 Phase 2 — Agent

| 序号 | 任务 | 产出/备注 |
|------|------|-----------|
| D2.1 | LangGraph 图：ReAct 或等价循环 | 状态与消息列表可测 |
| D2.2 | 工具：计算器、当前时间、**检索 tool**，以及基于 MySQL 的业务工具：`get_user_orders`、`get_user_coupons`、`handoff_to_human` | 至少一个工具在日志/响应中可验证 |
| D2.3 | 为工具层实现错误处理、超时与脱敏逻辑（避免把敏感字段直接暴露给模型） | 统一在 Tool 执行层处理 |
| D2.4 | 明确「自动 RAG 注入」与「仅 Agent 调检索/业务工具」两种路径的边界 | 写入 README 或代码注释 |

**阶段完成定义**：README 要求的「一步工具或一步检索后再答」可见；至少演示一次 MySQL 订单/优惠券工具调用与一次转人工（`handoff_to_human`）；超限安全停止。

补充运行边界（已实现约定）：
- `AGENT_ENABLED=false`：Phase 1 自动 RAG 注入路径。
- `AGENT_ENABLED=true`：Phase 2 Agent 工具路径（检索/订单/优惠券/转人工均在后端内部执行）。

---

### 2.4 Phase 3 — 硬化、流式与工程化

| 序号 | 任务 | 产出/备注 |
|------|------|-----------|
| D3.1 | LLM `stream` 实现 + API 或 CLI 其一可演示 | 前端流式为后续增强 |
| D3.2 | （可选）服务端会话基于 MySQL 存储 + 会话 id | 与前端 localStorage 策略协调 |
| D3.3 | Dockerfile 或多服务 compose 占位 | 开发/演示可复现 |
| D3.4 | CI：Python `ruff` + `pytest`；前端 `eslint` + `npm run build` | 分支保护逐步启用 |
| D3.5 | 更新 README：后端启动命令、环境变量表、与前端联调步骤、生产 CORS/HTTPS 注意点 | 与验收对齐 |

**阶段完成定义**：流式演示 + CI 绿灯 + 文档可照做跑通全链路。

---

### 2.5 前端并行项（非阻塞后端，但建议排期）

| 序号 | 任务 | 备注 |
|------|------|------|
| F1 | 若后端 `reply` 扩展为结构化（正文+引用数组），同步改 `chatApi.ts` 与 `MessageList` | 契约变更需版本或特性开关 |
| F2 | 流式 UI（SSE/fetch stream） | 依赖 D3.1 API 形态 |
| F3 | 登录对接平台真实 SSO | 替换演示 `localStorage` |

---

## 3. 测试计划

### 3.1 测试层级与工具

| 层级 | 范围 | 工具/方式 |
|------|------|-----------|
| **单元** | 分块边界、空文档、检索 Top-K、prompt 拼装、工具入参出参 | `pytest`，LLM/外部 HTTP **mock** |
| **集成** | ingest → persist → retrieve → 组装消息；小 fixture 知识库 | `pytest` + 临时目录/Chroma 测试库 |
| **API 契约** | `POST /api/chat`：合法 body 返回 200 + JSON 含 `reply`；非法 body 4xx | `pytest` + `httpx.AsyncClient` 或 `TestClient` |
| **前端静态** | 类型与构建 | `npm run build`、`npm run lint` |
| **手工回归** | 登录、空会话快捷问题、多轮、清空、退出、错误提示 | 每阶段发布前执行（见 3.3） |

**原则**：CI **不默认**调用真实付费 API；敏感用例用 `pytest -m integration` 或环境变量开关。

---

### 3.2 各阶段测试重点

| 阶段 | 必测点 |
|------|--------|
| Phase 0 | 配置缺失时明确失败；mock LLM 下 CLI 与 `/api/chat` 返回稳定；CORS 预检通过 |
| Phase 1 | 引用片段非空、来源字段正确；多轮 history 顺序正确；embedding 维度错误可发现 |
| Phase 2 | 步数上限触发；工具异常不崩溃；mock 固定路由时 Agent 行为可断言 |
| Phase 3 | 流式响应完整性（可选校验拼接结果）；Docker 内一次 smoke：`curl` chat |

---

### 3.3 手工回归清单（建议每次合并主干前勾选）

- [ ] 前端：`npm run dev`，未配置 `VITE_API_BASE_URL` 时演示回复正常  
- [ ] 前端：配置 `VITE_API_BASE_URL` 后发送消息，网络面板 200，气泡展示 `reply`  
- [ ] 登录：错误密码/短密码提示；登录后进聊天；刷新后会话（按当前实现为本地持久化）  
- [ ] 空会话：快捷问题点击后输入框内容与焦点正确  
- [ ] 后端（若有）：换 OpenAI/Ollama 配置各跑一次 CLI 或 API smoke  

---

## 4. 环境与数据

| 项 | 说明 |
|----|------|
| **Python** | 3.11+，虚拟环境或 uv |
| **Node** | 与 `frontend/package.json` 兼容的 LTS |
| **密钥** | 仅本机 `.env`，不入库；CI 用 secret 或 mock |
| **测试数据** | `tests/fixtures/` 下放短小 txt/md/pdf；勿放真实用户数据 |

补充说明：
- 运行独立向量写入时，默认目录由 `RAG_SEED_DIR` 控制（默认 `data/seed`）。
- 类型定义位于 `src/aicode_types.py`；避免创建 `src/types.py`（会与 Python 标准库冲突）。

---

## 5. 风险与缓解（测试视角）

| 风险 | 缓解 |
|------|------|
| 契约变更导致前端白屏 | 先改 `chatApi.ts` 与后端一致，再合并；契约测试锁住字段 |
| Flaky 集成测试 | Chroma/文件系统用独立临时目录；测试并行时隔离路径 |
| 真实 API 费用与限流 | CI 全 mock；夜间可选真实 smoke 工作流 |

---

## 6. 文档同步

| 变更类型 | 应更新文档 |
|----------|------------|
| 环境变量新增/重命名 | `README.md`、`config/` 示例、`developPaln.md` 环境表（若已摘录） |
| API 路径或 JSON 字段变更 | `frontend/src/lib/chatApi.ts` 注释、`README.md`、本文件 §2 |
| 阶段完成 | `plan.md` 检查清单打勾（或 issue/milestone） |

---

## 7. 参考链接（仓库内）

- 需求与验收：[README.md](./README.md)  
- 路线图与技术锁定：[plan.md](./plan.md)  
- 前端请求约定：[frontend/src/lib/chatApi.ts](./frontend/src/lib/chatApi.ts)  
