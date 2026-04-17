# 项目启动结果（前后端）

## 1. 启动环境

- 工作目录：`D:\Agents`
- Python 环境：`D:\software\anaconda\envs\aicode-py311`
- 后端项目：`D:\Agents\backend`
- 前端项目：`D:\Agents\frontend-copy`

---

## 2. 后端启动

### 启动命令（推荐）

在 `D:\Agents\backend` 下执行：

```powershell
& "D:\software\anaconda\envs\aicode-py311\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 19001
```

### 本次启动结果

- 后端可用地址：`http://127.0.0.1:19001`
- 健康检查接口：`GET /api/v1/health`

---

## 3. 前端启动

### 启动前配置

编辑 `D:\Agents\frontend-copy\.env`：

```env
VITE_API_BASE_URL=http://127.0.0.1:19001
```

### 启动命令

在 `D:\Agents\frontend-copy` 下执行：

```powershell
npm run dev -- --host 0.0.0.0 --port 5173
```

### 本次启动结果

由于 `5173/5174/5175` 端口已被占用，Vite 自动切换到了：

- 前端地址：`http://localhost:5176/`

---

## 4. 当前联调入口

- 前端：`http://localhost:5176/`
- 后端：`http://127.0.0.1:19001`

---

## 5. PyCharm 启动方式

### 5.1 后端（Run Configuration）

1. 打开 `D:\Agents\backend` 项目
2. `Run -> Edit Configurations... -> + -> Python`
3. 配置如下：
   - Name：`backend-uvicorn`
   - Python interpreter：`D:\software\anaconda\envs\aicode-py311\python.exe`
   - Module name：`uvicorn`
   - Parameters：`app.main:app --host 0.0.0.0 --port 19001`
   - Working directory：`D:\Agents\backend`
4. 点击 Run，启动后端

### 5.2 前端（PyCharm Terminal）

在 PyCharm 下方 Terminal 执行：

```powershell
cd D:\Agents\frontend-copy
npm run dev -- --host 0.0.0.0 --port 5173
```

如端口被占用，Vite 会自动切换到下一个可用端口（例如 `5176`）。

### 5.3 前端后端联调关键项

- 确保 `D:\Agents\frontend-copy\.env` 中：
  - `VITE_API_BASE_URL=http://127.0.0.1:19001`
- 若修改了 `.env`，请重启前端 dev server。

---

## 6. 快速验证

### 6.1 后端健康检查

```powershell
& "D:\software\anaconda\envs\aicode-py311\python.exe" -c "import requests;print(requests.get('http://127.0.0.1:19001/api/v1/health',timeout=5).status_code)"
```

预期返回：`200`

### 6.2 聊天接口验证

```powershell
& "D:\software\anaconda\envs\aicode-py311\python.exe" -c "import requests; r=requests.post('http://127.0.0.1:19001/api/v1/chat/message',json={'user_id':'player_002','text':'查询我的订单'},timeout=20); print(r.status_code); print(r.text)"
```

---

## 7. 常见问题

### 7.1 端口被占用（WinError 10048）

- 现象：后端/前端启动时报 `address already in use`
- 处理：
  1. 更换端口重启
  2. 或释放旧进程后再启动

### 7.2 查询类报错 `sql_generation_failed`

- 常见原因：云端模型额度不足（403）
- 当前策略：查询 Agent 已具备降级 SQL 模板能力，但需确保使用的是最新后端进程（重启后端生效）

