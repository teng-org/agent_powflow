# ADR-016：双产品控制台、用户资产与动态 API 发布

- 状态：Accepted
- 日期：2026-08-09
- 范围：Powflow 产品入口、资产权威、首次部署和公开运行时

## 1. 背景

当前默认开发栈同时暴露 `web-app`、`web-admin`、`debug-web`、`kb-admin-web` 以及三个后端端口。浏览器直接感知 Control、Execution、Chat 三个平面，造成登录态、权限、CORS、错误语义和配置来源分裂。知识库、Workflow、会话和运行也分别以“平台管理功能”和“用户资产”两种方式出现，产品边界不清。

Powflow 的目标不是复刻大厂算力底座，而是提供可自部署的 AI 应用构建平台：部署者配置第三方模型与存储，平台用户创建自己的知识库、Agent、Workflow，并把已发布版本通过 API 提供给外部应用。

## 2. 决策

### 2.1 只保留两个产品

1. **Powflow Platform Admin**（`apps/web-admin`）
   - 仅面向 `PLATFORM_ADMIN`。
   - 平台总用量、总用户、租户、权限、审计、风险、任务、死信和服务状态。
   - 全平台资产查询、冻结、转移、删除和细粒度审计。
   - 第三方 AI Provider/模型池、价格、预算、数据库、对象存储和运行参数。
   - 首次启动提供类似 WordPress 的安装向导。
2. **Powflow Workspace**（`apps/web-app`）
   - 面向 `USER`、`TENANT_ADMIN`，平台管理员可进入排障。
   - 用户自己的知识库、Agent、Workflow、对话测试、发布管理、API Key 和调用日志。
   - 同一用户可以创建多个 Agent 和 Workflow；资产必须同时记录 `tenant_id` 和 `owner_user_id`。

`apps/kb-admin-web` 继续保留源码用于短期回滚，但不进入默认部署。`apps/debug-web` 只允许开发 profile，不能作为产品功能入口。

### 2.2 外部入口收口

生产环境只公开：

- `console.<domain>`：用户工作台；
- `admin.<domain>`：平台后台；
- `api.<domain>`：公开调用数据面。

Control、Execution、Chat 的容器端口均为内部端口。浏览器和第三方客户端不得选择或拼接内部服务地址。

### 2.3 资产模型

所有用户资产至少包含：

- `tenant_id`：租户安全边界；
- `owner_user_id`：所有者；
- `visibility`：`PRIVATE` 或 `WORKSPACE`；
- `status`、`created_at`、`updated_at`；
- 不可变发布版本和审计主体。

核心关系：

```text
Tenant
  └─ User
      ├─ KnowledgeBase[*]
      ├─ Agent[*] ── AgentVersion[*]
      ├─ Workflow[*] ── WorkflowVersion[*]
      └─ Deployment[*]
          ├─ asset_type + asset_id + immutable_version
          ├─ stable_slug
          ├─ ApiCredential[*]
          └─ InvocationAudit[*]
```

### 2.4 发布和外部调用

不为每个 Agent/Workflow 创建端口或容器。统一使用稳定调用契约：

```http
POST /api/v1/deployments/{deployment_slug}/invoke
Authorization: Bearer pf_live_...
```

Control Plane 负责 Deployment、版本绑定、API Key 哈希、状态、资产授权和审计；Runtime 只执行 Control 下发的不可变快照。密钥只在创建时返回一次，服务端仅保存强哈希和可识别前缀。

边缘网关采用可插拔设计。首选 **Kong Gateway OSS**：利用其 Key Authentication、Consumer 和 Rate Limiting 能力承担 TLS、入口限流、请求体限制和基础消费者识别。业务级的 `key -> deployment -> tenant -> asset` 授权仍由 Powflow Control Plane 校验，避免把资产权威迁入网关。单机开发阶段允许直接使用 Control Plane 的公开调用端点；启用 Kong 后外部 URL 不变。

### 2.5 第三方 AI 与首次安装

首次安装向导分为：

1. 站点信息与首个管理员；
2. PostgreSQL、对象存储、Qdrant 和队列连接检查；
3. 第三方 AI Provider、模型、用途和凭据；
4. 默认模型路由、额度与单用户保护上限；
5. 最终连通性测试和配置发布。

数据库连接、主密钥等启动前配置只显示检查结果，不允许在运行中的网页里直接热改。Provider、模型路由、价格和限额属于运行配置，可在安装后继续治理。安装完成状态必须持久化；缺少必需 Provider 或密钥时，平台保持“未就绪”而不是静默使用弱默认值。

## 3. 权限约束

- `PLATFORM_ADMIN`：平台治理和跨租户资产审计；跨租户写操作必须单独审计。
- `TENANT_ADMIN`：当前租户成员和工作空间资产治理，无平台配置权限。
- `USER`：默认只能操作自己拥有的资产，可读取显式共享到工作空间的资产。
- 外部 API Key：只能调用绑定的 Deployment，不等价于用户 JWT，也不能访问 Console API。

任何查询先限定 `tenant_id`，再限定 owner/visibility；禁止先按资源 ID 查询后再做租户判断。

## 4. 迁移顺序

1. 备份当前源码工作树和完整 Git 历史。
2. 收敛默认部署为两个产品前端，debug/legacy 改为 profile。
3. 将现有后台导航拆为平台治理；用户资产能力迁入 Workspace。
4. 为 KB/Workflow 补齐 owner/visibility，并建立 Agent、Deployment、API Credential 表。
5. 提供用户资产 API 与公开调用 API；前端不再直连内部平面。
6. 建立首次安装向导和 Provider 连通性门禁。
7. 引入 Kong profile，完成限流、密钥轮换和公开域名验收。

## 5. 验收标准

- 默认部署只有两个产品入口，legacy/debug 不默认启动。
- 普通用户能创建多个 KB、Agent、Workflow，并只能看到自己或工作空间共享的资产。
- Agent/Workflow 发布后得到稳定 URL；API Key 可创建、轮换、吊销且明文不落库。
- 管理员能按租户、用户、资产和调用维度审计，并能配置全局 Provider 与存储检查。
- 新部署在完成安装向导前不能对用户宣称 AI 能力可用。
- Console 和公开 API 均不暴露或依赖 `8091/8092/8099` 等内部拓扑。
