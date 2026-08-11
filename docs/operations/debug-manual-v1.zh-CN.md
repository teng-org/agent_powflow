# 调试说明书 v1（本地联调）

- 文档版本：v1
- 适用日期：2026-02-09
- 项目：`agent_powflow`
- 适用场景：本地开发环境下，启动后端服务与管理前端并完成知识库联调

## 1. 目标

本说明书用于指导你在 Windows + Docker Desktop + Conda 环境下，完成以下调试目标：

- 启动基础依赖：PostgreSQL / MongoDB / RabbitMQ / MinIO / Qdrant
- 启动后端：Control Plane（Spring）+ Execution API（FastAPI）+ Worker（Celery）
- 启动前端：统一管理端 `apps/web-admin` + 接口调试端 `apps/debug-web`
- 跑通最小链路：管理员登录 -> 文档异步入库 -> 切片查看 -> 检索 -> Agent 执行

## 2. 环境前置

确保以下工具可用：

- Docker Desktop（已启动）
- Java 21 + Maven 3.9+
- Conda 环境：`agent-powflow-py311`
- Node.js 18+（用于前端）

项目根目录：`f:\my_projects\agent_powflow`

### 2.1 统一配置文件（推荐）

统一启动配置文件：

- `config/service-ports.json`（端口）
- `config/runtime-config.json`（运行参数：管理员账号、token、全局 KB、CORS、第三方 key）

统一启动入口脚本：

- `infra/scripts/start-stack.ps1`
- `infra/scripts/stop-stack.ps1`
- `infra/scripts/start-dev-all.ps1`（栈 + 前端）
- `infra/scripts/stop-dev-all.ps1`（停前端 + 栈）

## 3. 启动顺序

强烈建议按顺序启动，避免连接报错。

### 3.1 启动基础依赖

在项目根目录执行：

```powershell
.\infra\scripts\start-stack.ps1 -Build
```

或一键启动（推荐）：

```powershell
.\infra\scripts\start-dev-all.ps1 -BuildStack
```

检查状态：

```powershell
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

期望至少看到：

- `agentpowflow-postgres`
- `agentpowflow-mongodb`
- `agentpowflow-rabbitmq`
- `agentpowflow-minio`
- `agentpowflow-qdrant`

### 3.2 启动 Control Plane（可选）

如果你已使用 `start-stack.ps1`，control-plane 已在 docker 内启动，可跳过本节本地启动方式。

新开终端：

```powershell
cd services/control-plane
mvn spring-boot:run
```

健康检查：

```powershell
curl http://localhost:$env:CONTROL_PLANE_PORT/api/v1/system/health
```

### 3.3 启动 Execution API（可选）

如果你已使用 `start-stack.ps1`，execution-api 已在 docker 内启动，可跳过本节本地启动方式。

新开终端：

```powershell
$env:POSTGRES_DSN="postgresql://apf:apf@localhost:5432/agentpowflow"
$env:QDRANT_URL="http://localhost:6333"
$env:EXECUTION_ADMIN_TOKEN="dev-admin-token"
$env:EXECUTION_ADMIN_USERNAME="admin"
$env:EXECUTION_ADMIN_PASSWORD="admin123"
$env:GLOBAL_KB_ID="global-kb"
# 可选：启用 SiliconFlow 嵌入
# $env:SILICONFLOW_API_KEY="<your_token>"

cd services/execution-plane
D:\software\dev-tools\miniconda\envs\agent-powflow-py311\python.exe -m uvicorn api.app.main:app --host 0.0.0.0 --port $env:EXECUTION_API_PORT --reload
```

健康检查：

```powershell
curl http://localhost:$env:EXECUTION_API_PORT/api/v1/system/health
```

### 3.4 启动 Execution Worker（可选）

如果你已使用 `start-stack.ps1`，execution-worker 已在 docker 内启动，可跳过本节本地启动方式。

新开终端：

```powershell
$env:POSTGRES_DSN="postgresql://apf:apf@localhost:5432/agentpowflow"
$env:QDRANT_URL="http://localhost:6333"
$env:EXECUTION_ADMIN_TOKEN="dev-admin-token"
$env:GLOBAL_KB_ID="global-kb"
# 可选：启用 SiliconFlow 嵌入
# $env:SILICONFLOW_API_KEY="<your_token>"

cd services/execution-plane
D:\software\dev-tools\miniconda\envs\agent-powflow-py311\python.exe -m celery -A workers.app.celery_app:celery_app worker -Q parse_queue,embed_queue,index_queue,agent_queue -l info
```

### 3.5 启动 debug-web（Vue3，本地）

新开终端：

```powershell
cd apps/debug-web
npm install
npm run dev
```

浏览器打开：`http://localhost:5177`
浏览器打开：`http://localhost:$env:DEBUG_WEB_PORT`

### 3.6 启动统一 web-admin（Vue3）

新开终端：

```powershell
cd apps/web-admin
npm install
npm run dev
```

浏览器打开：`http://localhost:5174`

旧 `apps/kb-admin-web` 已退役，默认 Compose 不启动。仅在有明确负责人和到期时间的兼容回滚中，才允许同时使用 `--profile legacy-kb-admin` 与 `EXECUTION_LEGACY_KB_WRITE_ENABLED=true`；日常开发不得在旧前端新增功能。

## 4. 知识库管理调试路径（推荐）

优先在 `web-admin` 的“智能体 → 知识库”执行：

1. 管理员登录（默认 `admin/admin123`）
2. 上传文档并提交异步入库任务
3. 查询任务状态直到 `SUCCESS`
4. 刷新文档列表并查看切片
5. 执行重建索引 / 删除文档（可选）

然后在 `debug-web` 验证：

1. `Execution System -> Health`
2. `Knowledge Base -> 向量检索`
3. `Agent Runtime -> 执行 Agent`

说明：

- 知识库公开写接口位于 Control Plane `/api/v1/admin/kbs/*`，需要管理员 token；Execution 同类公开写默认返回 `410 LEGACY_KB_WRITE_RETIRED`
- 全局 Header 建议设置：
  - `X-Tenant-Id`
  - `X-Actor-Id`
  - `X-Source-Service`

## 4.1 审计日志落盘位置

- 宿主机目录：`logs/kb-audit/<YYYY-MM-DD>/`
- 日志文件：
  - `access.log`（全量请求）
  - `auth.log`（登录/登出/鉴权）
  - `security.log`（高风险操作/权限拒绝）
  - `error.log`（失败请求）

如果日志未生成，先检查：

- `config/runtime-config.json` 中 `execution_audit_enabled` 是否为 `true`
- `execution-api` 容器是否已重建启动（`start-stack.ps1 -Build`）

## 5. 快速验收标准

满足以下即说明联调成功：

- health 接口都返回 `200`
- API 池接口可增删改查并能设置默认端点
- 文档入库任务返回 `accepted + task_id`
- 任务状态最终为 `SUCCESS`
- 文档可查询到切片列表
- 检索返回非空 `evidence`
- Agent 返回 `answer + citations + metrics`

## 6. 常见问题排查

### 6.1 连接 PostgreSQL 失败

- 检查容器：`agentpowflow-postgres` 是否 `Up`
- 检查端口：`5432` 是否被占用
- 检查 Spring 环境变量：
  - `SPRING_DATASOURCE_URL`
  - `SPRING_DATASOURCE_USERNAME`
  - `SPRING_DATASOURCE_PASSWORD`

### 6.2 连接 Mongo/Qdrant 失败

- 确认 `POSTGRES_DSN`、`QDRANT_URL` 环境变量
- 确认容器端口：Mongo `27017`，Qdrant `6333`

### 6.3 Admin API 返回 401/403

- 缺少 `Authorization` 或 token 不一致
- 保证请求头与 `EXECUTION_ADMIN_TOKEN` 一致

### 6.4 没有检索结果

- 先确保执行过 `Ingest Document`
- 检查任务是否已 `SUCCESS`
- 当前为单全局库模式，不需要传 `kb_id`
- 观察返回是否为 `low_evidence=true`

### 6.5 Docker 全栈构建失败（镜像拉取 401）

- 常见于 registry mirror 鉴权问题
- 暂时使用 `docker-compose.dev.yml` 启动依赖 + 本地运行后端服务即可继续开发

## 7. 关闭与清理

停止本次依赖容器：

```powershell
.\infra\scripts\stop-stack.ps1
```

连同卷一起清理（谨慎）：

```powershell
.\infra\scripts\stop-stack.ps1 -WithVolumes
```

## 8. 建议下一步

- 将本说明书中的手动步骤收敛为脚本：
  - `infra/scripts/start-dev.ps1`
  - `infra/scripts/stop-dev.ps1`
- 为关键接口补充自动化冒烟测试（CI 可复用）
