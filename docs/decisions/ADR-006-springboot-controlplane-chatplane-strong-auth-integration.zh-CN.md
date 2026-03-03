# ADR-006：Spring Boot 用户体系 + chat-plane 强鉴权对接

- 状态：Accepted
- 日期：2026-03-02
- 决策方：架构 + 后端
- 关联：
  - `docs/decisions/ADR-003-tenant-isolation-v1.md`
  - `docs/decisions/ADR-004-runtime-and-queue-topology.md`
  - `docs/decisions/ADR-005-agent-core-chat-memory-tools-first.zh-CN.md`
  - `docs/design/springboot-controlplane-chat-multitenant-plan-v1.zh-CN.md`

## 1. 背景

当前仓库已具备 chat-plane 基础对话能力（会话、消息、SSE、取消、记忆策略），但鉴权仍是 MVP 方案：

1. chat-plane `require_auth` 仅校验 `Authorization` 是否存在，并信任 `X-Tenant-Id/X-Actor-Id`。
2. control-plane 当前 `SecurityConfig` 为 `permitAll`，尚未成为权威鉴权入口。
3. chat-plane 会话/运行已带 `tenant_id/actor_id` 并进行归属校验，但身份来源不够强，存在伪造上下文风险。

因此需要在继续用户侧能力迭代前，先完成“Spring Boot 用户体系 + chat-plane 强鉴权对接”。

## 2. 决策

采用“**Control Plane 权威认证发令牌 + chat-plane 强校验声明 + 外部入口收口**”方案。

### 2.1 认证与授权边界

1. Spring Boot Control Plane 作为唯一身份与权限权威：
   - 登录/刷新/登出
   - JWT 签发与失效策略
   - 租户与角色装载（RBAC）
2. chat-plane 不再信任外部 `X-Tenant-Id/X-Actor-Id`，只信任 JWT 声明：
   - `sub`（用户标识）
   - `tenant_id`
   - `roles` / `scope`
3. 外部客户端不直连 chat-plane 管理接口；通过 control-plane 或网关转发。

### 2.2 声明与上下文标准

统一最小声明字段：

1. `sub`
2. `tenant_id`
3. `roles`（数组）
4. `sid`（会话/登录态，可选）
5. `exp` / `iat`
6. `jti`

统一系统上下文：

1. `tenant_id`、`actor_id` 从 JWT 派生，不接受客户端覆盖。
2. `trace_id` 可接受外部透传，无则服务端生成。

### 2.3 chat-plane 强鉴权规则

1. 所有 `/api/v1/chat/*` 需要有效 JWT。
2. `session/run/message` 查询必须满足 `(tenant_id, actor_id)` 严格匹配。
3. 管理接口 `/api/v1/admin/chat/*` 仅允许包含管理员角色声明。
4. 禁止仅凭静态 admin token 调用管理接口（过渡期可保留开关，默认关闭）。

## 3. 方案选型对比

### 方案 A：保持现状（弱鉴权）

优点：改动小。  
缺点：无法满足多租户安全边界，风险高。  
结论：否决。

### 方案 B：chat-plane 自建完整用户体系

优点：独立性强。  
缺点：与 control-plane 重复建设，状态分裂。  
结论：否决。

### 方案 C：control-plane 权威 + chat-plane 令牌校验（本方案）

优点：边界清晰，演进成本低，可与现有 chat-plane 快速衔接。  
缺点：需处理跨服务 JWT 校验与迁移期兼容。  
结论：采纳。

## 4. 分阶段实施（必须按阶段）

### 阶段 0：准入检查（已满足，可开工）

1. chat-plane 已有会话/消息/run 数据模型与 tenant/actor 字段。
2. chat-plane 已实现接口归属校验逻辑（需替换身份来源）。
3. control-plane 已具备 Spring Security 基础骨架（需从 permitAll 升级）。

### 阶段 1：control-plane IAM 最小闭环

1. 落地 `tenant/user/role/user_role` 核心表。
2. 实现登录/刷新/登出接口与 JWT 签发。
3. 角色策略最小集：`PLATFORM_ADMIN / TENANT_ADMIN / USER`。

### 阶段 2：chat-plane 强鉴权改造

1. 新增 JWT 校验组件（共享密钥或 JWKS）。
2. `require_auth/require_admin` 改为基于声明解析，不再信任外部 tenant/actor 头。
3. 会话/运行接口统一校验 `(tenant_id, actor_id, role)`。
4. 管理接口静态 token 路径标记为 deprecated。

### 阶段 3：入口收口与灰度

1. 前端登录统一走 control-plane。
2. 调试前端保留“直连 chat-plane”开关，仅限本地开发。
3. 灰度期间支持双栈鉴权（可配置开关），默认优先 JWT。

### 阶段 4：清理与强制

1. 下线 chat-plane 静态 admin token 鉴权路径。
2. 下线外部可控的 `X-Tenant-Id/X-Actor-Id` 注入路径。
3. 完成审计字段统一（tenant/actor/jti/trace）。

## 5. 数据与安全约束

1. 任何跨租户访问一律 `403`，不得回退到默认租户。
2. JWT 过期与撤销必须生效（至少支持短期 token + refresh）。
3. 会话、消息、run 的查询接口必须 tenant-aware 且 actor-aware。
4. 所有关键操作写审计日志（登录、登出、权限拒绝、管理操作）。

## 6. 验收标准（DoD）

1. 未携带 JWT 或无效 JWT 调用 chat 接口返回 `401`。
2. 租户 A 用户无法读取租户 B 任意 session/message/run（`403`）。
3. 普通用户调用管理接口返回 `403`。
4. 前端登录后可正常创建会话、对话、查看历史。
5. 审计可追溯到 `tenant_id + user_id(sub) + trace_id + jti`。

## 7. 风险与缓解

1. 风险：迁移期 401/403 增多影响联调。  
   缓解：灰度开关 + 双栈兼容窗口 + 明确错误码。

2. 风险：前后端令牌来源不一致。  
   缓解：前端仅保留一个 token 来源（control-plane 登录态）。

3. 风险：调试工具绕过网关。  
   缓解：Debug Web 增加“auth source”指示与强校验提示。

## 8. 结论

项目当前状态**适合立即进入施工**，但必须遵循“先 IAM、再 chat-plane 强鉴权、再入口收口”的顺序。  
禁止继续扩展用户侧对话能力而不先完成强鉴权切换。

