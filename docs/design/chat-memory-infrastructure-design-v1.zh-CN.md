# Chat Memory 基础设施详细设计 v1

- 版本：v1
- 日期：2026-03-03
- 关联：`docs/decisions/ADR-009-chat-memory-fusion-strategy.zh-CN.md`、`docs/decisions/ADR-008-chat-plane-conversation-storage-and-persistence-optimization.zh-CN.md`
- 状态：Draft（可直接施工）

## 1. 目标与范围

目标：为对话记忆融合策略提供可扩展的数据与检索基础设施，满足：

1. 长对话高召回（早期事实可召回）
2. 低 token 注入成本（预算可控）
3. 强可观测（每层召回、命中、成本可统计）
4. 可迭代（后续可替换重排器、记忆模型，不推翻底层）

范围：`chat-plane` 侧数据层、检索层、任务层与配置层。

## 2. 逻辑分层（与 ADR-009 对齐）

1. L0 最近窗口（不落新库）
2. L1 会话摘要（会话级结构化总结）
3. L2 关键记忆项（结构化 memory item）
4. L3 用户画像与最近活动（用户级长期/短期）
5. L4 早期对话 RAG（会话级片段向量检索）
6. L5 关键词补偿索引（可先 PG FTS，后续可扩展）

## 3. 数据模型设计（PostgreSQL）

说明：以下表建议落在 `chat-plane` 同一 PostgreSQL 中，统一事务与审计。

### 3.1 会话摘要表（新增）

表：`chat_session_summaries`

- `summary_id BIGSERIAL PK`
- `session_id TEXT NOT NULL`（FK -> `chat_sessions.session_id`）
- `tenant_id TEXT NOT NULL`
- `actor_id TEXT NOT NULL`
- `summary_type TEXT NOT NULL`（`short|long|topic|decision`）
- `content TEXT NOT NULL`
- `source_message_start_id TEXT NULL`
- `source_message_end_id TEXT NULL`
- `version INT NOT NULL DEFAULT 1`
- `is_current BOOLEAN NOT NULL DEFAULT TRUE`
- `created_at TIMESTAMPTZ NOT NULL`
- `updated_at TIMESTAMPTZ NOT NULL`

索引：
- `(session_id, is_current, updated_at DESC)`
- `(tenant_id, actor_id, updated_at DESC)`

### 3.2 用户最近活动表（新增）

表：`chat_user_recent_activities`

- `activity_id TEXT PK`
- `tenant_id TEXT NOT NULL`
- `actor_id TEXT NOT NULL`
- `session_id TEXT NULL`
- `activity_type TEXT NOT NULL`（`query|tool|topic|feedback|task`）
- `activity_text TEXT NOT NULL`
- `importance DOUBLE PRECISION NOT NULL DEFAULT 0.5`
- `ttl_at TIMESTAMPTZ NULL`
- `created_at TIMESTAMPTZ NOT NULL`
- `updated_at TIMESTAMPTZ NOT NULL`

语义约束（重要）：
- 该表记录“跨会话画像信号（profile_signal）”，不是逐轮原始问答流水。
- 写入内容应为用户风格/领域/背景/偏好的摘要化信号，作为用户画像动态补丁层使用。
- 同一用户在短间隔内优先合并更新最新信号，避免无意义膨胀。

索引：
- `(tenant_id, actor_id, created_at DESC)`
- `(ttl_at)`（清理任务扫描）

### 3.3 关键记忆项扩展（扩展现有 `chat_memory_items`）

新增字段：
- `source_type TEXT NOT NULL DEFAULT 'conversation'`（`conversation|summary|user_profile|tool_result|imported`）
- `confidence DOUBLE PRECISION NOT NULL DEFAULT 0.5`
- `ttl_at TIMESTAMPTZ NULL`
- `version INT NOT NULL DEFAULT 1`
- `importance_v2 DOUBLE PRECISION NULL`（异步重排回写）
- `last_used_at TIMESTAMPTZ NULL`
- `use_count INT NOT NULL DEFAULT 0`

新增索引：
- `(session_id, created_at DESC)`
- `(session_id, importance DESC)`
- `(session_id, ttl_at)`

### 3.4 记忆融合审计表（新增）

表：`chat_memory_fusion_logs`

- `fusion_id TEXT PK`
- `run_id TEXT NOT NULL`（FK -> `chat_runs.run_id`）
- `session_id TEXT NOT NULL`
- `tenant_id TEXT NOT NULL`
- `actor_id TEXT NOT NULL`
- `budget_total_tokens INT NOT NULL`
- `budget_used_tokens INT NOT NULL`
- `layer_stats JSONB NOT NULL`（每层召回数、入选数、截断数、token占用）
- `top_items JSONB NOT NULL`（最终注入条目摘要）
- `created_at TIMESTAMPTZ NOT NULL`

索引：
- `(tenant_id, actor_id, created_at DESC)`
- `(session_id, created_at DESC)`

## 4. 会话级小型 RAG 设计（Qdrant + PG 元数据）

## 4.1 Qdrant 集合

集合命名：
- `chat_mem_{tenant_id}_{actor_id}`（用户级隔离）
- 或 `chat_mem_{tenant_id}` + payload 过滤（先推荐后者，减少集合爆炸）

向量点 payload 建议：
- `tenant_id`
- `actor_id`
- `session_id`
- `message_id`
- `chunk_id`
- `segment_type`（`dialog_segment|summary_segment|memory_item`）
- `created_at`
- `importance`
- `importance_v2`
- `ttl_at`
- `text_preview`

## 4.2 PG 片段表（检索可解释）

表：`chat_session_segments`

- `segment_id TEXT PK`
- `tenant_id TEXT NOT NULL`
- `actor_id TEXT NOT NULL`
- `session_id TEXT NOT NULL`
- `source_message_ids TEXT[] NOT NULL`
- `text_content TEXT NOT NULL`
- `token_estimate INT NOT NULL DEFAULT 0`
- `keywords TEXT[] NULL`
- `embedding_ref TEXT NULL`（Qdrant point id）
- `is_active BOOLEAN NOT NULL DEFAULT TRUE`
- `created_at TIMESTAMPTZ NOT NULL`
- `updated_at TIMESTAMPTZ NOT NULL`

索引：
- `(tenant_id, actor_id, session_id, created_at DESC)`
- GIN(`keywords`)（关键词召回）

## 5. 检索融合流程（运行时）

1. Query rewrite（提取实体/约束/时间）
2. L4 向量召回（session segments + memory points）
3. L5 关键词召回（PG FTS/GIN keywords）
4. 合并去重（按 segment_id/item_id）
5. Fusion Rank 打分：
   - `score = rel*w1 + importance*w2 + recency*w3 + confidence*w4`
6. 预算分配裁剪
7. 写入 `chat_memory_fusion_logs`

## 6. 生命周期与后台任务

后台任务：

1. `memory_summary_compactor`
- 触发：消息数阈值 / token阈值 / 手动触发
- 产出：`chat_session_summaries`

2. `memory_segment_builder`
- 触发：新消息或摘要更新
- 产出：`chat_session_segments` + Qdrant 向量点

3. `memory_importance_reranker`
- 触发：低峰批处理
- 产出：回写 `importance_v2/confidence`

4. `memory_ttl_cleaner`
- 清理过期 `activities/memory_items/segments`

5. `memory_conflict_resolver`
- 同槽位冲突时标记主版本（保留历史）

## 7. 配置项（runtime-config 建议新增）

- `memory_fusion_enabled`（bool）
- `memory_budget_total_tokens`
- `memory_budget_l0_ratio`
- `memory_budget_l1_ratio`
- `memory_budget_l2_ratio`
- `memory_budget_l3_ratio`
- `memory_budget_l4_ratio`
- `memory_budget_l5_ratio`
- `memory_segment_max_tokens`
- `memory_segment_overlap_tokens`
- `memory_rag_top_k`
- `memory_keyword_top_k`
- `memory_rerank_enabled`
- `memory_rerank_batch_size`
- `memory_ttl_default_hours`

## 8. 迁移策略

分三步迁移，确保可回滚：

1. 新增表/字段（兼容写）
2. 双写阶段（旧逻辑 + 新表）
3. 读路径切换到融合检索

回滚：读路径回退到旧记忆注入，保留新表数据。

## 9. 可观测与告警

指标：

- 每层召回量、入选量、命中率
- 注入 token 占用与截断率
- rerank 延迟与失败率
- TTL 清理量
- 冲突解决量

告警建议：

- `memory_fusion_logs` 连续空写
- rerank 失败率 > 阈值
- Qdrant 写入失败率 > 阈值

## 10. 安全与租户隔离

必须按 `tenant_id + actor_id` 过滤：

1. 检索请求
2. 记忆写入
3. 活动读取
4. 调试接口展示

禁止跨租户聚合查询默认开放，仅允许平台管理员显式审计路径。

## 11. 分阶段施工建议

### P1（先落地）
- `chat_session_summaries`
- `chat_user_recent_activities`
- `chat_memory_items` 扩字段
- `chat_memory_fusion_logs`
- 融合注入（不含 rerank）

### P2（增强）
- `chat_session_segments` + Qdrant 会话索引
- Query rewrite + 向量召回 + 关键词补偿

### P3（优化）
- 异步 LLM rerank
- 自动调参（基于命中率/成本）
- 更细粒度冲突治理

## 12. 验收口径

1. 长对话（>100轮）关键约束召回率提升明显。
2. 平均 prompt token 成本可控在预算阈值内。
3. debug-web 可查看融合层明细与统计。
4. 数据层具备可迁移、可回滚、可审计能力。
