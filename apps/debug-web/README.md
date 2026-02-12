# debug-web

Vue3 接口调试前端，用于统一测试 control-plane / execution-api。

## Run

```bash
npm install
npm run dev
```

默认地址：`http://localhost:5177`

## Features

- 侧边栏按业务分组接口
- 全局系统上下文头（`X-Tenant-Id`, `X-Actor-Id`, `X-Trace-Id`, `X-Source-Service`）
- 动态编辑 path/query/header/body
- 请求预览 + 响应头/响应体 + 状态/耗时
- 接口说明面板（中文用途、参数说明、返回说明、调试建议）
- 覆盖 Control Plane、Execution、管理员知识库接口、Agent、API Pool
