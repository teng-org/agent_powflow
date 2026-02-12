# retrieval

检索与向量接口实现。

## 当前实现

- `schemas.py`：入库与检索请求模型。
- `store.py`：PostgreSQL（文档/chunk/task 元数据）+ Qdrant（向量索引）
- `service.py`：文档切分、embedding 索引、向量相似度检索（cosine）
- embedding 来源：`models/embedding_provider.py`
  - 优先 SiliconFlow `/v1/embeddings`
  - 失败降级到本地 deterministic embedding

## 运行依赖

- PostgreSQL
- Qdrant

环境变量：

- `POSTGRES_DSN`
- `QDRANT_URL`
- `QDRANT_API_KEY`（可选）
