# Agent Powflow

企业知识库与智能体执行平台（Control Plane + Execution Plane + KB Admin Web + Debug Web）。

## 技术栈

- 后端：
  - `services/control-plane`：Java / Spring Boot
  - `services/execution-plane`：Python / FastAPI + Celery
- 数据与中间件：PostgreSQL、MongoDB、RabbitMQ、Qdrant、MinIO
- 前端：
  - `apps/kb-admin-web`：知识库管理前端
  - `apps/debug-web`：接口调试与数据库可视化前端
- 编排：Docker Compose（`infra/compose/docker-compose.stack.yml`）

## 目录结构

- `apps/`：前端应用
- `services/`：后端服务
- `infra/compose/`：容器编排
- `infra/scripts/`：启动/停止脚本
- `config/`：运行配置（端口、运行参数）
- `logs/`：运行日志（含 KB 审计日志）

## 环境要求

- Docker Desktop（含 Compose v2）
- PowerShell（Windows 开发脚本基于 `.ps1`）
- Node.js 18+（前端本地运行）

## 配置说明

### 1) 端口配置

- 文件：`config/service-ports.json`
- 用途：统一管理服务端口
- 同步到环境变量文件：

```powershell
. .\infra\scripts\load-service-ports.ps1 -WriteEnvFile
```

会生成：`config/service-ports.env`（可提交）

### 2) 运行参数配置

- 文件：`config/runtime-config.json`
- 用途：Execution API/Worker 的运行参数（管理员账号、JWT、嵌入模式、审计日志开关等）

启动 Docker 栈时会自动生成：`config/stack.generated.env`（本地生成，不提交）

### 3) 敏感信息

请不要在仓库中提交真实 API Key（例如 `siliconflow_api_key`，当前 api_key 作为测试使用，后续销毁）。
建议本地维护并在提交前检查。

## 一键启动（推荐）

```powershell
.\infra\scripts\start-dev-all.ps1 -BuildStack
```

该命令会：

1. 启动 Docker 栈（数据库 + 中间件 + 后端）
2. 启动 `debug-web`
3. 启动 `kb-admin-web`

常用停止：

```powershell
.\infra\scripts\stop-dev-all.ps1
```

仅停止前端，保留 Docker 栈：

```powershell
.\infra\scripts\stop-dev-all.ps1 -KeepStack
```

## 仅管理 Docker 栈

启动：

```powershell
.\infra\scripts\start-stack.ps1 -Build
```

仅生成环境配置，不启动：

```powershell
.\infra\scripts\start-stack.ps1 -GenerateOnly
```

停止：

```powershell
.\infra\scripts\stop-stack.ps1
```

停止并清理卷（会删除容器卷数据）：

```powershell
.\infra\scripts\stop-stack.ps1 -WithVolumes
```

## 访问地址（默认）

- Control Plane: `http://localhost:8099`
- Execution API: `http://localhost:8091`
- Debug Web: `http://localhost:5177`
- KB Admin Web: `http://localhost:5180`
- RabbitMQ 管理台: `http://localhost:15672`
- Qdrant HTTP: `http://localhost:6333`

如需修改，编辑 `config/service-ports.json` 后重启。

## 知识库审计日志

Execution API 支持审计日志，按天落盘：

- 宿主机目录：`logs/kb-audit/YYYY-MM-DD/`
- 文件：
  - `access.log`
  - `auth.log`
  - `security.log`
  - `error.log`

对应配置：`config/runtime-config.json`

- `execution_audit_enabled`
- `execution_audit_base_dir`

## Git 协作规范

### 分支流程

- 先拉主干：

```powershell
git checkout main
git pull
```

- 新建功能分支：

```powershell
git checkout -b feature/xxx
```

- 提交并推送：

```powershell
git add .
git commit -m "feat: xxx"
git push -u origin feature/xxx
```

### 已忽略的本地产物（不应提交）

仓库已通过 `.gitignore` 忽略以下类型：

- `**/node_modules/`
- `**/dist/`
- `config/dev-frontends.pids.json`
- `config/stack.generated.env`
- 各类缓存与本地环境文件（`.env.local`、`.pytest_cache` 等）

如果历史中曾提交过这类文件，需执行一次“从索引移除”：

```powershell
# Windows PowerShell 推荐：按已跟踪文件列表移除（保留本地文件）
git ls-files | rg "(^|/)node_modules/|(^|/)dist/" | ForEach-Object { git rm -r --cached --ignore-unmatch -- $_ }
git rm --cached -- config/dev-frontends.pids.json config/stack.generated.env
git commit -m "chore: remove tracked local/build artifacts"
```

## 常见问题

### `docker compose up -d --build` 看起来“没反应”

通常是拉取基础镜像超时或网络代理问题。优先检查：

- Docker Desktop 网络/代理设置
- 是否可访问 `registry-1.docker.io`
- 是否在企业网络需要 HTTPS 代理

### `401 Unauthorized (/api/v1/admin/kbs)`

- 先登录获取 JWT（Debug Web 有快捷入口）
- 检查请求头：`Authorization: Bearer <token>`
- 检查租户与角色配置是否匹配

### 知识库页面无法连后端（`ERR_CONNECTION_REFUSED`）

- 检查 `execution-api` 容器是否启动
- 检查 `config/service-ports.json` 与前端请求端口一致
- 检查 CORS 配置：`execution_cors_origins`

## 说明

- 当前项目处于高频迭代阶段，优先使用脚本启动，避免手工参数漂移。
- 若你更新了 `runtime-config.json` 或 `service-ports.json`，建议重启相关服务以生效。
