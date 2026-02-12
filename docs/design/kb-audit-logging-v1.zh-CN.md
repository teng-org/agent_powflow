# 知识库审计日志设计 v1（企业级）

- 版本：v1
- 日期：2026-02-12
- 适用范围：`kb-admin-web` + `execution-api`（知识库相关接口）
- 目标：满足“可追责、可审计、可检索、可归档、可合规”

## 1. 设计目标

1. 记录所有知识库接口调用，包含登录与登出。
2. 可溯源到租户、用户、角色、权限决策、行为对象、结果与耗时。
3. 请求头与请求体必须记录“摘要信息”，不直接落原始 JSON。
4. 错误调用必须带错误原因与上下文线索。
5. 日志按天分文件，支持归档、检索、告警与长期留存。

## 2. 日志类型

1. `access`：所有请求的审计主日志（成功/失败都写）。
2. `auth`：登录、登出、鉴权失败、权限拒绝。
3. `security`：高风险行为（删除库、批量删除文档、权限异常）。
4. `error`：服务异常、外部依赖异常、业务异常。

说明：`access` 是权威流水；`auth/security/error` 是专题索引，便于 SOC 与审计查询。

## 3. 日志行格式（非 JSON）

采用 `LTSV`（`key:value`，以 `\t` 分隔）单行文本。  
优点：可读、可 grep、可被 ELK/Loki/ClickHouse 直接解析，且不落整段 JSON。

示例：

```text
ts:2026-02-12T10:23:41.125+08:00	level:INFO	log_type:access	event:kb.document.upload	request_id:2f0c...	trace_id:40ab...	tenant_id:platform	actor_id:kb-admin	actor_role:PLATFORM_ADMIN	auth_result:ALLOW	permission:kb:document:write	method:POST	path:/api/v1/admin/kbs/{kb_id}/documents/upload	headers:x-tenant-id=platform;x-actor-id=kb-admin;authorization=Bearer***;x-trace-id=40ab...	body:file_count=3;total_size_mb=12.4;ext=pdf,docx;chunk_size=500;chunk_overlap=50	resource_type:document	resource_id:doc-3a1f...	outcome:SUCCESS	http_status:200	biz_code:ACCEPTED	duration_ms:142
```

失败示例：

```text
ts:2026-02-12T10:24:03.919+08:00	level:ERROR	log_type:error	event:kb.document.upload	request_id:87da...	trace_id:e92f...	tenant_id:platform	actor_id:kb-admin	actor_role:PLATFORM_ADMIN	auth_result:ALLOW	permission:kb:document:write	method:POST	path:/api/v1/admin/kbs/{kb_id}/documents/upload	headers:x-tenant-id=platform;x-actor-id=kb-admin;authorization=Bearer***	body:file_count=1;total_size_mb=24.7;ext=pdf	resource_type:document	resource_id:doc-unknown	outcome:FAIL	http_status:400	biz_code:FILE_TOO_LARGE	duration_ms:35	error_class:ValidationError	error_reason:file too large (> 20MB)
```

## 4. 字段结构（标准）

1. 公共上下文字段
- `ts`：ISO8601 含时区
- `level`：`INFO|WARN|ERROR`
- `log_type`：`access|auth|security|error`
- `event`：业务事件名，如 `kb.auth.login`、`kb.document.upload`
- `service`：`execution-api`
- `env`：`dev|test|prod`
- `request_id`：单请求唯一 ID
- `trace_id`：跨服务链路 ID
- `source_service`：调用来源（如 `kb-admin-web`）

2. 身份与权限字段
- `tenant_id`
- `actor_id`
- `actor_role`
- `auth_result`：`ALLOW|DENY|ANON`
- `permission`：权限码，如 `kb:document:write`
- `policy_id`：权限策略版本（可选）

3. 请求摘要字段
- `method`
- `path`：路由模板，不含敏感参数值
- `query`：白名单摘要（如 `kb_id=...;limit=100`）
- `headers`：白名单摘要（见第 6 节）
- `body`：结构化摘要（见第 6 节）
- `client_ip`
- `user_agent`

4. 行为与结果字段
- `resource_type`：`kb|document|chunk|task|auth|setting`
- `resource_id`：业务对象 ID（必要时脱敏）
- `action`：`create|read|update|delete|upload|reindex|login|logout|commit|cancel`
- `outcome`：`SUCCESS|FAIL|REJECT`
- `http_status`
- `biz_code`：业务结果码
- `duration_ms`

5. 错误字段（失败必填）
- `error_class`：异常类
- `error_code`：内部错误码
- `error_reason`：可读原因（简洁）
- `upstream`：外部依赖（如 `postgres|qdrant|rabbitmq|siliconflow`）

## 5. 接口事件命名规范

1. 登录：`kb.auth.login`
2. 登出：`kb.auth.logout`
3. 创建库：`kb.kb.create`
4. 重命名库：`kb.kb.rename`
5. 删除库：`kb.kb.delete`
6. 文档上传：`kb.document.upload`
7. 文档删除：`kb.document.delete`
8. 文档重建：`kb.document.reindex`
9. Chunk 草稿编辑/删除/提交：`kb.chunk.draft.update|clear|commit`
10. Task 列表、取消、清空：`kb.task.list|cancel|clear`

## 6. 请求头/请求体摘要规则（核心）

1. 请求头记录白名单
- 记录：`x-tenant-id`、`x-actor-id`、`x-trace-id`、`x-source-service`、`content-type`、`content-length`
- 脱敏：`authorization` 仅记录 `Bearer***`
- 不记录：`cookie`、`set-cookie`、完整 token

2. 请求体记录摘要，不落原文
- 登录：`username_hash`、`login_channel`，不记录密码
- 新建/重命名库：`name_len`、`name_hash`
- 上传：`file_count`、`total_size_mb`、`ext_list`、`chunk_size`、`chunk_overlap`
- ingest/reindex：`doc_id`、`filename_hash`、`content_len`、`content_sha256_12`
- chunk 编辑：`chunk_id`、`text_len`、`text_sha256_12`

3. 响应体记录摘要
- 记录：`biz_code`、`task_id/doc_id/kb_id`、计数类字段（如 `accepted_count`）
- 不记录：长文本、向量、原始文档内容、完整异常栈

## 7. 落盘与目录结构（按天）

目录建议：

```text
logs/
  kb-audit/
    2026-02-12/
      access.log
      auth.log
      security.log
      error.log
      manifest.sha256
```

规则：

1. 每天 `00:00` 新建目录与文件。
2. 单文件超过 200MB 自动分片：`access.log.1`、`access.log.2`。
3. 当天结束生成 `manifest.sha256`（文件校验摘要）。
4. 保留策略：
- 热数据 30 天本地可查
- 温数据 180 天压缩归档
- 冷数据对象存储 1-3 年（按合规要求）

## 8. 错误与审计保障

1. 失败必写 `error_reason`，且含 `trace_id`。
2. 鉴权失败（401/403）必须写入 `auth.log` 与 `security.log`。
3. 高风险操作（删除库、批量删除文档、清空任务）额外写 `security.log`。
4. 日志写入失败不能影响主流程，但必须写本地兜底 `logs/kb-audit/fallback.log`。

## 9. 实现方式（FastAPI 侧）

1. 中间件统一采集
- 入站：生成 `request_id`，提取上下文、请求头摘要、请求体摘要
- 出站：补充 `http_status`、`duration_ms`、`outcome`

2. 路由级补充
- 用装饰器或路由映射提供 `event/action/resource_type/permission`
- 失败时从 `HTTPException` 或业务异常提取 `biz_code/error_reason`

3. 异步写日志
- 应用内 `asyncio.Queue` + 后台写线程
- 批量 flush（如 100 条或 1 秒）降低 IO 抖动

4. 专题分流
- 主日志写 `access.log`
- 按条件复制到 `auth.log` / `security.log` / `error.log`

## 10. 权限映射建议（知识库）

1. `kb:auth:login`：匿名允许，失败写安全日志
2. `kb:kb:read`：`PLATFORM_ADMIN|KB_MANAGER|AUDITOR`
3. `kb:kb:write`：`PLATFORM_ADMIN|KB_MANAGER`
4. `kb:system:write`：仅 `PLATFORM_ADMIN`
5. `kb:task:clear`：建议仅 `PLATFORM_ADMIN`

## 11. 性能与安全建议

1. 默认全量审计，不采样。
2. 敏感字段统一掩码组件，禁止各处手写。
3. 禁止记录向量、原文、密钥、密码、完整 token。
4. 对日志目录设置最小权限（仅读写给服务账号）。
5. 预留对接 SIEM（ELK/Loki/ClickHouse）字段一致性。

## 12. 与当前项目的落地清单

1. 在 `services/execution-plane/api/app/main.py` 增加 `AuditMiddleware`。
2. 新增 `services/execution-plane/api/app/audit_logging.py`：
- 摘要提取器
- 脱敏器
- LTSV formatter
- 按天分文件 writer
3. 为关键接口绑定 `event/action/permission` 元信息映射。
4. 增加 `POST /api/v1/admin/auth/logout`，写 `kb.auth.logout`。
5. 在 `config/runtime-config.json` 新增日志配置：
- `audit.enabled`
- `audit.base_dir`
- `audit.retention_days`
- `audit.max_file_mb`
- `audit.masking.version`

