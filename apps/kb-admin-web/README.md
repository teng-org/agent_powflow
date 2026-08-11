# kb-admin-web

> 退役状态：该应用已从默认 Compose 栈移出，仅可通过 `legacy-kb-admin` profile 临时启动。Execution 公共 KB 写入口默认返回 `410 LEGACY_KB_WRITE_RETIRED`；新操作必须使用统一 `apps/web-admin` Console 与 Control Plane API。兼容窗口只允许在明确设置 `EXECUTION_LEGACY_KB_WRITE_ENABLED=true` 时开启。

知识库后台管理前端（Vue3），用于“单一全局知识库 + 管理员维护”模式。

## 运行

```bash
npm install
npm run dev
```

默认地址：`http://localhost:5180`

## 功能

- 管理员登录（调用 `POST /api/v1/admin/auth/login`）
- 文档异步入库（上传文本 + chunk 参数）
- 查询任务状态
- 文档列表管理
- 切片查看
- 文档重建嵌入/索引
- 删除文档

## 依赖后端

- `execution-api`（默认 `http://localhost:8090`）
- `execution-worker`
- `RabbitMQ`
- `MongoDB`
- `Qdrant`
