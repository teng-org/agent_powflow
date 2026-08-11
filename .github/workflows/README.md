# workflows

GitHub Actions CI/CD 与质量守护流程。

## `quality-gate.yml`

在 `main` 推送和所有 Pull Request 上执行：

- 四个 Vue 应用使用锁文件安装依赖并执行测试（如有）与生产构建；
- Chat Plane / Execution Plane 执行 Python 语法编译；
- Chat Plane 执行标准库单元测试；
- 调试端接口目录与三个后端平面的路由做一致性校验；
- Docker Compose 配置解析校验；
- Control Plane 使用 Java 21 执行 Maven 测试。

工作流采用并发取消策略，同一分支的新提交会取消旧的在途质量检查。
