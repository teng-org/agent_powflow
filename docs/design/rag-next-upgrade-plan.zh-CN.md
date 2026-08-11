# Powflow RAG Next 架构审计与升级方案

日期：2026-08-11

## 1. 结论

Powflow 当前 RAG 可以支撑小规模、文本质量较好、问题以语义匹配为主的知识库问答，但其主检索仍属于 Dense Vector Top-K 时代的基础实现。系统工程基础并不过时：租户隔离、PostgreSQL 发布屏障、Qdrant 文档级整代替换、对象存储治理、失败修复账本、查询改写、引用与无证据保护均有价值，应保留。

真正落后的部分集中在检索质量层：文档理解过浅、切片缺少结构、知识库仅有单路 Dense 召回、没有候选融合与专用重排、索引缺少模型/版本清单、上下文组装缺少去重与多样性控制、没有离线质量评测门禁。

最优升级方向不是立即替换 Qdrant 或全面采用 GraphRAG，而是在现有架构上建立 `Parse → Multi-representation Index → Hybrid Recall → Fusion → Rerank → Context Packing → Grounded Generation → Evaluation` 的可观测管线。

## 2. 当前实现画像

| 环节 | 当前实现 | 判断 |
|---|---|---|
| 文档解析 | pypdf 文本、DOCX 段落、HTML 去标签、普通文本解码 | 简单文档可用；版面、表格、扫描件不足 |
| 切片 | fixed/sentence/paragraph/markdown/delimiter，字符长度 + overlap | 可配置但非 token/结构感知 |
| Embedding | 标准 embeddings API，严格模式；KB 内固定维度 | 基础可靠，缺模型版本与灰度 |
| 向量索引 | tenant+KB 独立 Qdrant collection，cosine | 隔离清楚，但只有单 Dense 向量 |
| 召回 | 单查询向量 Top-K，最多额外取 3 倍候选过滤脏数据 | 无稀疏/关键词通道，无融合 |
| 排序 | Qdrant cosine 顺序；可选固定阈值 | 无 reranker，阈值未校准 |
| 查询理解 | 启发式或可选 LLM rewrite；Agent 可多轮调用 RAG | 值得保留，但无多查询融合 |
| 上下文 | 顺序注入最多 8 个 chunk，字符预算 | 无去重、邻块扩展、文档配额和位置编排 |
| 引用/保护 | 返回 doc/chunk/score；可在无证据时拒答 | 基础较好，缺 claim-level 校验 |
| 评测 | 安全/接口/引用传播测试 | 缺检索和回答质量基准 |

### 必须立即修正的名实不符

前端允许 `vector / hybrid / rrf`，配置 schema 也暴露相关枚举，但运行时无论选择哪种策略都只调用同一 Dense 检索函数，`rank_method` 仅被写入 trace。这会让用户以为已启用混合检索，应在真正实现前将未支持选项隐藏或明确标记为实验不可用。

## 3. 目标架构

```text
原始文件
  → 版面/结构解析
  → Content Blocks（标题、段落、表格、代码、图片说明、页码、ACL）
  → Parent/Child + Token-aware 切片
  → 索引清单 Index Manifest
      ├─ Dense 向量
      ├─ Sparse/lexical 向量
      ├─ 文档/章节摘要向量（可选）
      └─ ColBERT 多向量（高质量模式，可选）

用户问题
  → 查询分类/改写
  → Dense + Sparse 并行召回
  → RRF/DBSF 融合与去重
  → Cross-encoder 或 late-interaction 重排
  → 邻块扩展、父块回填、每文档配额、多样性控制
  → Token 精确预算与证据位置编排
  → 生成、引用、无证据策略
  → 离线/在线质量评测与反馈
```

## 4. 分阶段实施

### P0：检索真实性与质量基线

目标：先让任何后续升级都能被量化，预计 3–5 个工作日。

1. 建立每类知识库至少 50–100 条问题的 Golden Set，记录相关 doc/chunk、可回答性和参考答案。
2. 增加离线检索指标：Recall@5/10/20、MRR@10、nDCG@10、无答案误召率。
3. 增加生成指标：context precision/recall、faithfulness、answer relevance、引用覆盖率、拒答准确率。
4. 记录线上 trace：query 原文/改写来源、候选数、各阶段耗时、各通道排名、最终证据、index revision；不记录敏感全文。
5. 在真正支持前隐藏或禁用 `hybrid/rrf`，避免配置幻觉。
6. 将 `score_threshold` 改为检索 profile 的校准参数，不允许跨 embedding 模型直接复用。

P0 验收：同一评测集可复现；每次索引/模型变更产生对比报告；发布门禁能阻止显著回退。

### P1：Hybrid Retrieval v2

目标：用最低架构成本解决精确词、编号、专有名词和语义问题的互补召回，预计 1–2 周。

1. Qdrant collection 改为命名向量：`dense` + `sparse`，保留 payload filter。
2. 使用现有 BGE-M3 能力时，不再只消费标准 Dense 输出；增加可返回 sparse lexical weights 的模型适配器。若外部 API 不暴露 sparse 输出，则部署独立 sparse encoder，不能伪造支持。
3. Dense 与 Sparse 各预取 30–80 个候选，用 Qdrant Query API 做 RRF；先用 rank fusion，避免直接混合不可比较的原始分数。
4. 对融合候选做 chunk_id 去重、近重复去重、每文档候选上限和最小文档多样性控制。
5. 新增 reranker adapter：先对融合后的 30–60 个候选重排，最终保留 6–12 个。默认可使用中文/多语言 cross-encoder；高价值场景再评估 BGE-M3 multi-vector/ColBERT。
6. Search API 明确区分 `candidate_k`、`rerank_k`、`final_k`，不再用一个 `top_k` 同时表达三个阶段。

P1 验收建议：相对当前 Dense 基线，Recall@10 不下降，MRR/nDCG 有统计显著提升；精确编号类问题 Recall@10 明显提升；P95 检索延迟和 rerank 成本在预算内。

### P2：文档理解与结构化切片

目标：解决“检索算法没问题，但入库内容已经坏掉”的上限问题，预计 2–3 周。

1. 引入可插拔 parser profile：`fast-text`、`layout-aware`、`ocr-required`。
2. 解析产物不再只是单个字符串，而是带 `page/section_path/block_type/bbox/table_id/language` 的 Content Blocks。
3. PDF 保留页码、标题和版面；扫描件走 OCR；DOCX 读取标题、列表、表格；代码/Markdown 保留结构。
4. 使用 token-aware 的 Parent/Child 切片：小块用于召回，父章节或邻块用于最终上下文。
5. 将标题路径、文档标签、时间、可见范围和业务元数据写入 Qdrant payload，支持发布前过滤。
6. `embedding_max_chars=380` 不再二次无语义滑窗拆块；按模型 tokenizer 和真实 token 限制生成索引单元。

P2 验收：随机抽样解析完整率、表格单元恢复率、页码引用准确率；复杂 PDF 的检索指标相对 P1 再提升。

### P3：上下文化索引与自适应检索

目标：提升代词、省略、跨章节和多跳问题，预计 2–4 周，必须受评测集驱动。

1. 为 child chunk 附加短上下文：文档名、章节路径、关键实体和时间；分别保留 `display_text` 与 `retrieval_text`，引用仍展示原文。
2. A/B 测试 contextual embedding 与 late chunking，不直接全量替换。
3. 查询路由器区分：无需检索、精确查找、语义问答、多跳、全局归纳。
4. 精确查找走 Sparse 优先；语义问答走 Hybrid；多跳允许受限的 query decomposition；低置信度时执行一次 corrective retrieval 或明确拒答。
5. Context packer 合并相邻块、回填父块、按文档配额控制多样性，并把高相关证据放在上下文首尾，缓解 lost-in-the-middle。

### P4：可选高级检索器

以下能力只按知识库 profile 开启，不作为默认：

- GraphRAG/DRIFT：适合“整个语料的主题、关系、趋势是什么”等全局问题；不适合普通 FAQ 默认启用。
- RAPTOR/层级摘要：适合长文档跨层级归纳，但需验证摘要失真和索引成本。
- ColBERT/多向量：适合高价值、强精确度场景；需评估存储、GPU 和查询延迟。
- 多模态文档检索：当图片、图表、扫描表格是核心知识时启用，不能靠文本 OCR 一条链路覆盖。

## 5. 索引版本与无停机迁移

新增 `index_manifest`，至少包含：

- `index_revision`
- `parser_id/parser_version`
- `chunker_id/chunker_version/config_hash`
- `dense_model_id/version/dimension`
- `sparse_model_id/version`
- `reranker_id/version`
- `created_at/status/document_count/chunk_count`

每次大版本构建影子 collection，例如 `apf_{tenant}_{kb}_r2`。新旧索引双读评测，达到门禁后用 KB 的 `active_index_revision` 原子切换；失败时可立即回滚。禁止在当前 collection 上通过逐文档重嵌入完成跨模型升级，因为混合版本会使分数不可比并破坏阈值语义。

## 6. Search API 建议

请求至少增加：

```json
{
  "kb_id": "...",
  "query": "...",
  "retrieval_profile": "hybrid-v2",
  "candidate_k": 60,
  "rerank_k": 30,
  "final_k": 8,
  "filters": {"doc_ids": [], "tags": [], "time_range": null},
  "debug": false
}
```

证据响应增加但保持向后兼容：

```json
{
  "doc_id": "...",
  "chunk_id": "...",
  "page": 12,
  "section_path": ["第三章", "权限模型"],
  "scores": {"dense": 0.82, "sparse_rank": 2, "rrf": 0.031, "rerank": 0.91},
  "index_revision": "r2",
  "text": "原始展示文本"
}
```

普通用户响应不暴露内部调试分数；工作台调试页和管理员质量面板可按权限查看。

## 7. 不建议现在做的事

1. 不因“新”就直接全面 GraphRAG；它解决的不是所有问答类型。
2. 不先把 top_k 从 6 提到几十；长上下文会增加成本、噪声和中部信息丢失。
3. 不用 LLM rerank 替代所有检索器；先使用稳定、可批处理、可评测的 reranker。
4. 不在无版本字段的旧 collection 上混合新旧 embedding。
5. 不把文档摘要向量当作 chunk 检索的替代；摘要适合路由/全局检索，不适合细粒度事实引用。
6. 不继续保留实际无效的 UI 选项。

## 8. 推荐的近期落地顺序

第一批应只做 P0 + P1：评测集、真实 rank_method、Dense/Sparse/RRF、reranker、去重/配额、index manifest 与影子索引。完成后再用数据决定是优先投入版面解析、上下文化切片，还是全局 GraphRAG。

## 9. 2026-08-11 实施状态

已落地并部署：

- V10 `rag_index_manifests`、Chunk `index_revision` 与 PostgreSQL GIN 词法特征；
- Dense、Keyword、Hybrid/RRF 的真实独立分支，候选数、最终数、文档配额和分通道分数；
- Chat、Agent、Workflow 统一策略透传和检索 trace；
- 旧索引无重算 Dense embedding 的分批回填，开发库已升级 5,066 个 Chunk；
- `context_window` 邻接上下文扩展，保留命中 Chunk、原文和引用身份；
- JSONL 离线评测 CLI，支持 Recall@K、MRR@K、nDCG@K 和阈值门禁。
- SiliconFlow `BAAI/bge-reranker-v2-m3` Cross-Encoder 重排，支持强制成功和显式降级；
- Content Block 结构化解析与 Parent/Child 持久化，上下文优先回填父块；
- V12 revision-scoped PostgreSQL child/parent 快照、每版本独立 Qdrant collection；
- 影子构建完整性门禁、可选 Golden Set 检索门禁、事务内原子激活和任意保留版本回滚；
- 活动版本首次快照、后续文档增量发布、跨所有索引版本的文档/知识库删除清理；
- Control/Execution 管理 API：版本列表、构建、评测、激活、回滚和失败版本清理。

当前仍明确未完成：学习型 sparse encoder、复杂 PDF 的版面/表格/OCR 专用解析、真实新 embedding 模型的整库重算、检索策略 A/B 流量和在线反馈闭环。现有词法通道仍应称为 deterministic lexical/BM25，不宣称为 BGE-M3 learned sparse；当前影子构建复用已持久化 Dense embedding，适用于检索策略与索引结构升级，不冒充跨 embedding 模型迁移。

这一顺序能最大限度复用现有 PostgreSQL、Qdrant、MinIO、Celery 和 Chat trace 基础，同时把每项质量收益变成可验证指标。

## 10. 研究依据

- Qdrant Hybrid Queries: https://qdrant.tech/documentation/search/hybrid-queries/
- Anthropic Contextual Retrieval: https://www.anthropic.com/engineering/contextual-retrieval
- BGE-M3: https://arxiv.org/abs/2402.03216
- ColBERTv2: https://arxiv.org/abs/2112.01488
- Late Chunking: https://arxiv.org/abs/2409.04701
- Lost in the Middle: https://arxiv.org/abs/2307.03172
- RAGAS: https://arxiv.org/abs/2309.15217
- GraphRAG: https://www.microsoft.com/en-us/research/publication/from-local-to-global-a-graph-rag-approach-to-query-focused-summarization/
