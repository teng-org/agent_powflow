# .github 目录说明

本目录用于存放 **GitHub 平台级配置**，包括工作流（GitHub Actions）、Issue/PR 模板、协作规范入口文档等。

目标：让仓库协作流程标准化，减少“人肉约定”。

---

## 目录结构

当前：

- `.github/README.md`：本说明文档
- `.github/workflows/`：CI/CD 工作流目录
- `.github/workflows/README.md`：工作流目录说明

建议后续补齐：

- `.github/ISSUE_TEMPLATE/`：问题/需求模板
- `.github/PULL_REQUEST_TEMPLATE.md`：PR 模板
- `.github/CODEOWNERS`：代码所有者规则
- `.github/dependabot.yml`：依赖更新策略

---

## 本仓库协作约定（GitHub 侧）

### 1) 分支策略

- 受保护分支：`main`
- 开发分支命名：`feature/<topic>`、`fix/<topic>`、`chore/<topic>`
- 禁止直接向 `main` 推送，必须走 PR

建议示例：

- `feature/kb-task-progress`
- `fix/qdrant-point-detail`
- `chore/readme-refresh`

### 2) 提交流程

1. 从 `main` 拉最新：
   - `git checkout main`
   - `git pull`
2. 新建分支开发：
   - `git checkout -b feature/xxx`
3. 提交（建议使用 Conventional Commits）：
   - `feat: ...`
   - `fix: ...`
   - `chore: ...`
   - `docs: ...`
4. 推送并创建 PR：
   - `git push -u origin feature/xxx`

### 3) PR 要求

每个 PR 至少包含：

- 变更目的（为什么改）
- 变更范围（改了哪些模块）
- 验证方式（如何测试）
- 风险与回滚方案（可选但推荐）

涉及前端交互变更时，建议附截图或录屏。

---

## CI/CD 设计建议（放在 `.github/workflows/`）

当前仓库尚未完善 CI，建议分阶段上线：

### Phase 1: 基础校验（必选）

- Python 语法检查与基础测试
- 前端构建检查（`apps/debug-web`、`apps/kb-admin-web`）
- 提交前静态检查（如有 lint 配置）

### Phase 2: 质量守护（推荐）

- PR 必须通过 CI 才可合并
- 按路径触发（仅改前端时不跑后端重测试）
- 依赖缓存（提升速度）

### Phase 3: 发布流程（可选）

- Tag 触发镜像构建
- 产物版本化
- 可回滚部署记录

---

## 与本仓库运行脚本的关系

开发启动脚本在 `infra/scripts/`，GitHub Actions 不直接复用本地 PowerShell 脚本时，建议在 workflow 中使用等价命令实现。

本地常用：

- `./infra/scripts/start-dev-all.ps1 -BuildStack`
- `./infra/scripts/stop-dev-all.ps1`

---

## 安全与合规

请确保以下内容 **不进入版本库**：

- 本地构建产物：`dist/`、`node_modules/`
- 本地运行态文件：`config/dev-frontends.pids.json`、`config/stack.generated.env`
- 敏感密钥与凭据（API Key、生产密钥）

若历史中已提交敏感文件：

1. 立即替换密钥（rotate）
2. 追加 `.gitignore` 规则
3. 视情况执行历史清理（`git filter-repo`）

---

## 维护清单

建议每月检查一次：

- 是否存在失效工作流
- PR 模板是否仍适配当前开发流程
- CODEOWNERS 是否覆盖新增模块
- CI 时长是否可优化

---

## 快速导航

- 仓库主文档：`README.md`
- 工作流目录：`.github/workflows/README.md`
- 运行配置：`config/runtime-config.json`
- 端口配置：`config/service-ports.json`
