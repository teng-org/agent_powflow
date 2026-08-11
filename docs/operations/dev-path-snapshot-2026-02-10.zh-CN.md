# 当前开发路径快照（2026-02-10）

- 日期：2026-02-10
- 阶段：知识库前后端联调（MVP）
- 适用分支：当前工作区（未提交）

## 1. 本轮目标与完成情况

### 1.1 已完成
- 知识库管理前端改为产品化单页布局（登录后进入管理页）。
- 文档上传改为真实文件上传（`multipart/form-data`），不再前端强制文本解码。
- 后端新增常见文档解析：`txt/md/html/pdf/docx`。
- 文档入库保持异步任务链路（任务状态通过 SSE 驱动前端通知）。
- 前端移除 Task 面板，改为顶部悬浮通知（按任务最终状态通知）。
- 弹窗交互统一：底部按钮右对齐，取消即退出，不再保留 `Close` 按钮。
- 上传弹窗重构：仅保留拖拽/点选上传区 + 待上传文件列表 + 移除按钮。
- 新增单文件上传大小限制：默认 `50MB`，并提供后端配置 API 可修改。
- Execution Plane 数据主存从 MongoDB 迁移为 PostgreSQL（知识库/文档/chunk/task/api 池）。
- 保留 Qdrant 作为向量索引，RabbitMQ 作为异步队列。

### 1.2 当前默认约束
- 单文件最大体积：`50MB`
- 支持上传扩展名：`.txt .md .markdown .html .htm .pdf .docx`
- 超限文件：前端本地拦截 + 后端二次校验（双保险）

## 2. 关键接口快照

### 2.1 文档上传与入库
- `POST /api/v1/admin/kbs/{kb_id}/documents/upload`
  - `multipart/form-data`
  - 参数：
    - `files`（可多个）
    - `chunk_size`（可选，默认 500）
    - `chunk_overlap`（可选，默认 50）
  - 返回：
    - `accepted_count`
    - `skipped_count`
    - `accepted[]`
    - `skipped[]`（含失败原因）

### 2.2 上传限制配置（预留后台系统管理）
- `GET /api/v1/admin/system/upload-settings`
- `PUT /api/v1/admin/system/upload-settings`
  - body:
  ```json
  { "max_file_size_mb": 50 }
  ```
  - 权限：`PLATFORM_ADMIN`

## 3. 数据与配置落点

### 3.1 PostgreSQL
- 表：`system_settings`
  - `settings_key = upload_limits`
  - `value_json.max_file_size_mb`
  - `updated_at`
  - `updated_by`

- 核心业务表：
  - `knowledge_bases`
  - `documents`
  - `chunks`
  - `tasks`
  - `task_events`
  - `chunk_drafts`
  - `api_pool_settings`
  - `api_pool_endpoints`

### 3.2 执行层依赖新增
- `pypdf==5.2.0`
- `python-docx==1.1.2`
- `python-multipart==0.0.20`
- `psycopg[binary]==3.2.4`

## 4. 前端行为快照（知识库管理页）

- 登录后进入知识库管理页。
- 顶部悬浮通知只提示：
  - 任务完成（成功）
  - 任务失败（错误摘要）
  - 任务取消
- 不再逐步骤刷通知。
- 页面弱化非必要信息（隐藏 `#` 编号等噪音）。

## 5. 你回来后的测试清单（建议顺序）

1. 管理员登录知识库页面。
2. 上传 1 个小 `txt` 文件（验证正常链路）。
3. 上传 1 个 `pdf` 与 1 个 `docx`（验证解析与切片可读性）。
4. 上传 1 个超过 `50MB` 的文件（验证前端拦截与后端兜底）。
5. 调用 `PUT /api/v1/admin/system/upload-settings` 调整上限。
6. 回到上传弹窗确认上限显示已更新，再次上传接近上限的文件。
7. 检查最终通知是否仅在任务完成/失败时弹出。

## 6. 已知风险与下一步

- 当前 `docx/pdf` 解析为纯文本，复杂排版（表格、图片文本）可能损失结构信息。
- 后续可补：
  - 分类型解析策略（保留页码/段落结构）
  - OCR 能力（扫描版 PDF）
  - 后台“系统设置页”可视化配置上传上限
  - 文件级病毒扫描/类型白名单加强
