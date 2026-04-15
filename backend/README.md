# Multi Agent 客服后端（FastAPI）

## 运行环境
- Python: `D:\software\anaconda\envs\aicode-py311`

## 安装依赖
```powershell
& "D:\software\anaconda\envs\aicode-py311\python.exe" -m pip install -r requirements.txt
```

## 启动服务
```powershell
& "D:\software\anaconda\envs\aicode-py311\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
```

## 环境变量
- 在 `backend/.env` 配置全局变量（可参考 `backend/.env.example`）。
- 当前已纳入 `.env` 管理的关键项：
  - `SERVICE_NAME`
  - `SESSIONS_PERSISTENCE`
  - `INTENT_AGENT_USE_LOCAL` / `INTENT_AGENT_*`
  - `SEARCH_AGENT_USE_LOCAL` / `SEARCH_AGENT_*`
  - `RAG_AGENT_USE_LOCAL` / `RAG_AGENT_*`
  - `SUMMARIZER_AGENT_USE_LOCAL` / `SUMMARIZER_AGENT_*`
  - `CHROMA_PATH`
  - `RAG_TOP_K`
  - `MOCK_ORDER_BASE_URL`

## 关键接口
- `GET /api/v1/health`
- `POST /api/v1/chat/message`
- `POST /api/v1/orders/confirm`
- `POST /api/v1/orders/finalize`

## 订单强管控说明
- `collect_info -> awaiting_pre_confirm -> executed_waiting_click -> closed`
- 默认不调用支付 API，不扣余额。
- 执行失败会返回失败原因。
- 执行成功后返回订单链接，需用户点击确认后结束流程。
