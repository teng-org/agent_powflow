- project: agent_powflow
- repo_create_at: 2026-2-9

- 开发提醒：在 vscode 左下角看分支是否非 `main`
  - 如果是 main ,请使用 `git checkout -b feature/xxx` 创建你目前正在工作的分支并开发
  - 使用 `git branch` 查看当前分支
  - 提交代码到当前分支 ：`git add .` `git commit -m "feat: xxx"`
  - 推送代码到 github 对应分支: `git push -u origin feature/xxx`

- 开始开发: 使用 `git checkout main` `git pull` 来拉取最新的代码
- 然后使用 `git checkout -b feature/xxx` 开切换到你的工作分支



API：conda run -n agent-powflow-py311 uvicorn api.app.main:app --reload --port 8090（在 services/execution-plane 目录）


Worker：conda run -n agent-powflow-py311 celery -A workers.app.celery_app:celery_app worker -Q parse_queue,embed_queue,index_queue,agent_queue -l info

Java 侧：services/control-plane 的 mvn -DskipTests package 成功。

. .\infra\scripts\load-service-ports.ps1 -WriteEnvFile


.\infra\scripts\start-stack.ps1 -Build          启动
.\infra\scripts\start-stack.ps1 -GenerateOnly   仅生成配置（不启动）
.\infra\scripts\stop-stack.ps1                  停止
.\infra\scripts\stop-stack.ps1 -WithVolumes     停止并清理卷

.\infra\scripts\start-dev-all.ps1 -BuildStack   一键启动：docker 栈 + debug-web + kb-admin-web
.\infra\scripts\stop-dev-all.ps1                一键停止：前端进程 + docker 栈
.\infra\scripts\stop-dev-all.ps1 -KeepStack     只停前端，保留 docker 栈

知识库审计日志（execution-api）：
- 配置项：`config/runtime-config.json`
  - `execution_audit_enabled`
  - `execution_audit_base_dir`
- Docker 默认落盘目录（宿主机）：`logs/kb-audit/YYYY-MM-DD/*.log`
  - `access.log`
  - `auth.log`
  - `security.log`
  - `error.log`
