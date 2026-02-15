# Agent Powflow

企业知识库与智能体执行平台，包含 Control Plane、Execution Plane、KB Admin Web、Debug Web。

## 1. 项目功能清单

### 1.1 知识库管理（KB Admin Web + Execution API）
- 多知识库管理：创建、重命名、删除、文档迁移。
- 文档管理：上传、仅上传、清洗/切片/嵌入、删除、标签管理。
- 切片管理：按策略切片、重切片、切片草稿编辑、提交草稿、预删除。
- 嵌入管理：文档嵌入、重嵌入、切片提交后的增量嵌入。
- 总结管理：文档总结、库总结、重总结、总结脏状态提示。
- 批量操作：批量重切片/重嵌入/重总结/移动/删除/打标签。
- 任务面板：实时任务流、子任务展开、取消、重试、清理、批次追踪。

### 1.2 任务与执行
- 执行链路：`解析 -> 切片 -> 嵌入 -> 索引 -> 总结`（按任务类型触发子集）。
- Celery 异步任务编排，支持并发与取消。
- 幂等/防重：任务去重锁（防重复提交）。
- 任务状态同步：管理 API + SSE 任务流。

### 1.3 模型与 API 池
- 主 API 池（main）：embedding / llm 端点管理。
- 辅助 API 池（aux）：summary_llm / summary_embedding 等端点管理。
- 支持 OpenAI 兼容接口接入（如 SiliconFlow）。
- 模型价格支持两级配置：
  - 端点级：`input_per_1k` / `output_per_1k`（优先）
  - 全局级：`runtime-config.api_model_pricing`（兜底）

### 1.4 用量、成本与预算
- API token 用量统计（30 天窗口）：
  - 按天、按 API、按模型、按任务类别、按 KB 聚合
  - 事件明细查询（分页、过滤）
- 成本估算：按模型单价估算 `estimated_cost_usd`
- 预算与告警：
  - 预算策略（scope）
  - 预算评估
  - 告警列表与状态更新（OPEN/RESOLVED）

### 1.5 可观测与审计
- 审计日志落盘：访问、鉴权、安全、错误按天分文件。
- Debug Web：
  - 接口调试控制台
  - 数据库可视化（PostgreSQL / Qdrant / MongoDB）
  - 系统状态页（任务、API 池、配置）
  - Runtime Config 在线查看与修改

---

## 2. 系统架构与技术栈

### 2.1 服务与组件
- `services/control-plane`：Java / Spring Boot
- `services/execution-plane`：Python / FastAPI + Celery
- `apps/kb-admin-web`：知识库管理前端（Vue）
- `apps/debug-web`：调试与可视化前端（Vue）
- 中间件/存储：
  - PostgreSQL（结构化业务数据）
  - Qdrant（向量索引）
  - MongoDB（部分扩展存储）
  - RabbitMQ（任务队列）
  - MinIO（对象存储）

### 2.2 核心数据流
1. 文档上传/入库
2. 解析与清洗
3. 切片生成
4. 向量嵌入
5. 写入 Qdrant 索引
6. 文档总结/库总结
7. 前端任务面板与状态页实时反馈

---

## 3. 目录结构说明

```text
agent_powflow/
├─ apps/                         # 前端应用
│  ├─ kb-admin-web/              # 知识库管理前端
│  ├─ debug-web/                 # API调试 + DB可视化 + 状态页
│  ├─ web-admin/                 # 其他管理端（预留/扩展）
│  └─ web-app/                   # 其他业务前端（预留/扩展）
├─ services/                     # 后端服务
│  ├─ control-plane/             # 控制面
│  └─ execution-plane/           # 执行面（API + Worker）
├─ infra/
│  ├─ compose/                   # Docker Compose 编排
│  ├─ docker/                    # 镜像构建相关
│  ├─ k8s/                       # K8s 清单（如有）
│  └─ scripts/                   # 启停与开发脚本
├─ config/                       # 运行配置与端口映射
├─ logs/                         # 运行日志（含 kb-audit）
├─ docs/                         # 项目文档
├─ tools/                        # 开发工具脚本
└─ README.md
```

---

## 4. 启动与运行

## 4.1 环境要求
- Docker Desktop（Compose v2）
- PowerShell（Windows 脚本）
- Node.js 18+（前端本地开发）

### 4.2 一键开发启动（推荐）

```powershell
.\infra\scripts\start-dev-all.ps1 -BuildStack
```

该命令会启动：
1. Docker 栈（后端 + 数据库 + 中间件）
2. `apps/debug-web`
3. `apps/kb-admin-web`

停止：

```powershell
.\infra\scripts\stop-dev-all.ps1
```

仅停前端，保留容器：

```powershell
.\infra\scripts\stop-dev-all.ps1 -KeepStack
```

### 4.3 仅管理 Docker 栈

```powershell
.\infra\scripts\start-stack.ps1 -Build
.\infra\scripts\stop-stack.ps1
.\infra\scripts\stop-stack.ps1 -WithVolumes
```

---

## 5. 配置体系

### 5.1 端口配置
- 文件：`config/service-ports.json`
- 生成环境映射：

```powershell
. .\infra\scripts\load-service-ports.ps1 -WriteEnvFile
```

生成：
- `config/service-ports.env`
- `config/stack.generated.env`（本地生成）

### 5.2 运行参数
- 文件：`config/runtime-config.json`
- 典型参数：
  - 管理员账号/JWT
  - 审计开关
  - 嵌入与总结策略参数
  - API 并发槽位参数
  - `api_model_pricing`（模型价格兜底）

### 5.3 API 池默认配置
- 主池：`services/execution-plane/config/api_pool.default.json`
- 辅助池：`services/execution-plane/config/aux_api_pool.default.json`
- 端点级价格：`input_per_1k` / `output_per_1k`

---

## 6. 默认访问地址

- Control Plane: `http://localhost:8099`
- Execution API: `http://localhost:8091`
- Debug Web: `http://localhost:5177`
- KB Admin Web: `http://localhost:5180`
- RabbitMQ 管理台: `http://localhost:15672`
- Qdrant HTTP: `http://localhost:6333`

---

## 7. 审计日志

按天落盘：
- 目录：`logs/kb-audit/YYYY-MM-DD/`
- 文件：
  - `access.log`
  - `auth.log`
  - `security.log`
  - `error.log`

相关参数：
- `execution_audit_enabled`
- `execution_audit_base_dir`

---

## 8. 开发协作建议

### 8.1 分支流程

```powershell
git checkout main
git pull
git checkout -b feature/xxx
git add .
git commit -m "feat: xxx"
git push -u origin feature/xxx
```

### 8.2 忽略产物
已通过 `.gitignore` 忽略：
- `**/node_modules/`
- `**/dist/`
- `config/dev-frontends.pids.json`
- `config/stack.generated.env`
- 本地缓存与临时文件

---

## 9. 常见问题

### 9.1 401 Unauthorized
- 检查 admin 登录是否成功
- 检查 `Authorization: Bearer <token>`
- 检查租户与角色

### 9.2 前端连不上后端
- 检查 `execution-api` 容器状态
- 检查 `service-ports.json` 与前端请求端口
- 检查 `execution_cors_origins`

### 9.3 配置修改不生效
- 修改 `runtime-config.json` / 端口配置后重启相关服务

---

## 10. 当前定位

项目处于高频迭代期，建议优先通过 `infra/scripts` 脚本管理启动与停止，避免手工参数漂移。
