# Agent Powflow

面向团队和企业的 AI 应用构建平台，提供知识库、RAG、智能体、Workflow、Skill、MCP、JavaScript 工具与 OpenAI-compatible API 发布能力。

当前版本：`v0.2.0-beta.1`（生产预演 Pilot）。适合在独立 Rocky Linux 服务器上进行小范围受控实测，暂不建议承载公开商用、高敏感或关键业务数据。

## 主要服务

| 服务 | 默认地址 | 用途 |
| --- | --- | --- |
| Web App | `http://localhost:5173` | 用户控制台 |
| Web Admin | `http://localhost:5174` | 平台管理控制台 |
| KB Workbench | `http://localhost:5180` | 知识库与 RAG 工作台 |
| Control Plane | `http://localhost:8099` | 身份、权限、资产与发布 |
| Execution Plane | `http://localhost:8090` | 文档、检索、工具与 Workflow 执行 |
| Chat Plane | `http://localhost:8092` | 对话、记忆与 Agent 运行时 |

基础设施包括 PostgreSQL、MongoDB、RabbitMQ、MinIO 与 Qdrant。数据库迁移文件只定义空库结构演进，不包含现有数据库或用户数据。

## 本地启动

```powershell
Copy-Item .env.example .env
docker compose --env-file .env -f infra/compose/docker-compose.stack.yml up -d --build
```

真实调试必须保持：

```text
CHAT_PROVIDER_MODE=api_strict
WORKFLOW_PROVIDER_MODE=api_strict
```

## Rocky Linux 部署

将 `.env.production.example` 复制到服务器仓库之外，例如 `/etc/agent-powflow/agent-powflow.env`，填入独立强密钥并设置权限：

```bash
sudo install -d -m 750 /etc/agent-powflow
sudo install -m 600 .env.production.example /etc/agent-powflow/agent-powflow.env
sudo vi /etc/agent-powflow/agent-powflow.env
```

执行预检、部署和服务验证：

```bash
sudo bash deploy/rocky/preflight.sh
sudo bash deploy/rocky/deploy.sh --env-file /etc/agent-powflow/agent-powflow.env --build
sudo bash deploy/rocky/check-services.sh --env-file /etc/agent-powflow/agent-powflow.env
```

生产 Compose 覆盖会隐藏数据库、消息队列、对象存储、Qdrant 与内部后端端口。持久数据位于 Docker named volumes，不会进入 Git 仓库。

## 仓库内容边界

仓库仅保留产品源码、测试源码、数据库迁移定义、Compose/环境模板，以及 Rocky Linux 部署工具。以下内容不提交：

- 设计文档、开发过程文档和 Agent 指令文件；
- 通用开发运行脚本和本地审计脚本；
- 数据库文件、Dump、备份、对象、向量索引和运行日志；
- `.env`、API Key、JWT、数据库密码及其他真实凭据。

版本变化见 [CHANGELOG.md](CHANGELOG.md)。
