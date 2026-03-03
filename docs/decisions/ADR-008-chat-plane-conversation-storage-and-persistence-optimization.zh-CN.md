# ADR-008：Chat-Plane 对话数据存储与持久化性能优化

- 状态：Accepted
- 日期：2026-03-03
- 决策者：Agent Powflow 核心开发组
- 关联：`ADR-005`、`ADR-006`、`ADR-007`

## 背景
`chat-plane` 在对话量上升后暴露出以下问题：

1. 写放大：高频消息下存在全量持久化路径，导致不必要的 I/O 与数据库压力。
2. 连接开销：PostgreSQL 写入路径频繁建连，增加延迟与失败概率。
3. 可观测性不足：无法直观看到 store 是否脏、何时落盘、写入次数与连接复用状态。
4. 运维控制不足：缺少“立即刷盘”能力，不利于调试与下线前一致性保障。

## 决策
对 `chat-plane` 持久化层进行分层优化：

1. 存储模型保持“PG 规范化表为主 + 本地 JSON 镜像兜底”。
2. PostgreSQL 写路径改为增量写（按对象 upsert/delete），不再每次全量同步。
3. 本地 JSON 写入改为合并写与定时刷盘（默认 `chat_history_disk_flush_interval_ms=800`）。
4. PostgreSQL 引入连接复用与失败自动重连，减少每次写入建连成本。
5. 新增可观测与运维接口：
   - `GET /api/v1/admin/chat/store/status`
   - `POST /api/v1/admin/chat/store/flush`
6. Debug Web 同步接入上述接口与状态展示，提供在线可视化与手动 flush。

## 数据模型与真相源
### 主数据（PostgreSQL）
- `chat_sessions`
- `chat_messages`
- `chat_session_memory`
- `chat_user_profiles`
- `chat_memory_items`
- `chat_memory_rerank_jobs`
- `chat_runs`

### 镜像数据（文件）
- `chat_history_store.json`

约定：业务查询与分析以 PostgreSQL 为准；JSON 文件用于本地观察、容灾兜底与调试。

## 关键实现要点
1. 增量写入覆盖以下操作：
   - 创建/更新/删除会话
   - 新增消息
   - 更新会话记忆与用户画像记忆
   - 新增与重排记忆条目
   - 运行状态 upsert
2. 刷盘节流：
   - 内存变更后仅标记 dirty，并在节流窗口内合并落盘。
   - 支持 `force flush` 立即落盘。
3. 连接复用：
   - store 内维护可复用 PG 连接。
   - 连接异常后失效并在后续写入自动重连。
4. schema 初始化：
   - 启动后按需检查建表，结果缓存，失败按重试间隔回退。

## 观测指标（store/status）
- `persist_backend`
- `postgres_dsn_configured`
- `postgres_available`
- `postgres_connection_reused`
- `disk_dirty`
- `disk_flush_scheduled`
- `disk_write_count`
- `pg_write_count`
- `last_disk_save_at`
- `last_pg_save_at`
- `counts.*`（sessions/messages/memory/runs）

## 影响与权衡
### 正向影响
1. 显著降低写放大和无效 I/O。
2. 降低 PG 建连抖动带来的尾延迟。
3. 提升排障效率（状态可见 + 可手动 flush）。

### 负向影响
1. 引入“延迟刷盘”窗口，极端崩溃下可能丢失节流窗口内未落盘镜像数据。
2. store 逻辑复杂度上升，需要更明确的状态监控。

### 风险控制
1. PG 仍为主真相源，JSON 仅镜像与兜底。
2. 关键场景（停机前、压测后）可调用 `store/flush`。
3. 异常时自动重连并回退 schema 检查。

## 验收标准
1. 高频对话压测下，PG 写入不再出现每条消息全量同步。
2. `store/status` 可反映 dirty/scheduled/write_count/connection_reused 实时变化。
3. `store/flush` 可将 dirty 状态清零并更新 `last_disk_save_at`。
4. debug-web 可直接调用并展示上述状态。

## 回滚策略
1. 保留全量快照代码路径（`_save_to_postgres_locked`）用于紧急恢复或离线校准。
2. 若增量路径出现一致性问题，可临时切换为 `postgres_only` 并强制 flush + 重建镜像。

## 后续任务
1. 增加持久化集成测试（并发写、异常重连、force flush）。
2. 增加 metric 导出（Prometheus）并纳入告警。
3. 在 Memory 阶段引入 `importance_v2`（规则分 + 异步 LLM 重排）并落库。

## 落地增补（2026-03-03）
1. `chat_memory_items` 已扩展冲突治理字段：
   - `conflict_state`（`active` / `conflict_shadow`）
   - `shadowed_by_item_id`
2. 异步重排任务表已落地：`chat_memory_rerank_jobs`
   - 字段覆盖：任务归属（tenant/actor/session）、请求参数（limit）、状态、结果、错误、生命周期时间戳。
3. Store 可观测已补充：
   - `counts.memory_rerank_jobs`（内存镜像中的任务数）。
