# 真实 LLM 演示

这个目录用于本地切换到真实 OpenAI 兼容模型做演示，不影响默认的 mock 演示流程。

## 使用方式

1. 复制环境模板到项目根目录的 `.env`。

```powershell
Copy-Item demo\real_llm\.env.real.example .env
```

2. 编辑项目根目录 `.env`，填入真实 `ECOV3_OPENAI_API_KEY`。

3. 启动服务。

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

4. 打开健康检查确认已经切到真实模型模式。

```text
http://127.0.0.1:8000/health
```

当返回中 `llm_mode` 为 `remote` 时，表示主智能体决策已经走真实远程 LLM。

## 说明

- 默认 UI 和观测面板仍可继续使用。
- `ECOV3_MCP_MOCK_ENABLED=true` 会保留当前内置 MCP mock 能力，便于演示完整链路。
- 如果你只想做稳定录屏，建议继续使用默认 mock 模式。
