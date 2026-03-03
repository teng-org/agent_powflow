# web-app

用户侧对话前端（Vue3）。

## 功能

- 直连 `chat-plane` 的 SSE 流式对话。
- 可切换 KB 挂载来源（从 execution-api 拉取 KB 列表）。
- 可配置 rank 方法（vector/hybrid/rrf）与 top_k。
- 支持对话任务取消。
- 展示当前生效 prompt（来自 chat-plane config）。

## Run

```bash
npm install
npm run dev
```

默认地址：`http://localhost:5173`
