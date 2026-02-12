# models

LLM 与 Embedding 模型封装层。

## 当前实现

- `llm_provider.py`: Mock LLM provider（后续替换真实 OpenAI/本地模型）。
- `api_pool.py`: API 池管理（默认配置加载 + CRUD + 按 `task` 默认端点选择）。
- `embedding_provider.py`: Embedding provider（优先 SiliconFlow API，失败自动本地兜底）。

## 默认配置

- `services/execution-plane/config/api_pool.default.json`

可通过环境变量 `API_POOL_DEFAULT_CONFIG` 指向自定义配置文件。

SiliconFlow token 可通过：

- API 池 `api_key` 字段
- 或环境变量 `SILICONFLOW_API_KEY`
