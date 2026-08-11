# Powflow Console 交互与无障碍基线 v1

日期：2026-08-04

## 1. 范围与结论

本轮聚焦 `apps/web-admin` 的应用壳层、登录、顶部/侧边导航、通用自定义下拉和监控弹窗。已完成基础键盘操作、可读语义、焦点可见性、动效降级和三档响应式收口；这是一轮工程基线，不代表完整 WCAG 合规认证。

## 2. Before / After

| 项目 | Before | After |
|---|---|---|
| 登录 | 普通容器和点击按钮，回车链路不可验证 | 原生 `form`、submit、required、autocomplete、回车提交、busy/status/alert |
| 顶部导航 | `javascript:void(0)` 伪链接 | 语义按钮、`nav` 名称、`aria-current=page`、清晰焦点 |
| 侧边导航 | 当前项和展开层级主要依赖视觉 | page/location、expanded/controls、分区名称、连接状态 live region |
| 自定义下拉 | 仅指针点击，多个控件同名 | 完整键盘模型、禁用项跳过、焦点恢复、唯一名称、listbox/option 状态 |
| 动效 | 悬停和开合未区分输入方式 | 键盘无开合动画，hover 仅精细指针，按压反馈，reduced-motion 降级 |
| 内容跳转 | 无绕过重复导航入口 | 可聚焦“跳到主要内容”链接与可接收焦点的主内容 |
| 弹窗关闭 | 读屏名称为 `x` | 按业务上下文提供“关闭事务日志中心”等名称，图形符号隐藏 |
| Modal 焦点 | 背景仍可聚焦，各页面自行处理关闭 | 16 个弹窗统一首焦点、Tab 圈定、Escape、背景 inert、滚动锁、嵌套栈和焦点恢复 |
| 可点击表格行 | 只能鼠标点击整行，键盘无法到达 | 16 类数据行可聚焦、有上下文名称、Enter/Space 激活并暴露当前态 |
| 表格分页 | 各页面自行拼接按钮和页码，状态不会统一播报 | `UiPager` 统一首页/前后页/末页、边界禁用、页码与条数 live region；覆盖 6 类分页视图 |
| 表格空态 | 仅显示视觉文案 | 主要数据表空态统一为 polite、atomic `status`，异步刷新后可被辅助技术感知 |
| 时间列切换 | 点击整个表头切换相对/精确时间，语义上近似“可排序表头” | 使用原生按钮并暴露当前模式和目标模式，不再伪装为排序行为 |
| 响应式证据 | 主要依赖 CSS 静态检查 | 1280、820、390 三档真实浏览器几何与横向溢出检查 |

## 3. 键盘契约

`UiSelect` 触发器支持 ArrowUp/ArrowDown、Home/End、Enter/Space 和 Escape。选项支持方向键循环、Home/End、Enter/Space 选择、Escape 取消、Tab 离开；禁用项不会获得活动焦点。选择或取消后焦点回到触发器。键盘路径通过关闭 Vue 过渡，避免输入反馈被动画延迟。

## 4. 验证记录

| 检查 | 结果 |
|---|---|
| Vitest | 13 个文件，43/43 通过；新增分页边界、页码夹取和状态文案模型测试 |
| Vite production build | 通过；90 modules；主入口约 190.28 kB / gzip 60.65 kB |
| 回车登录 | 真实浏览器通过，登录中状态可见，最终进入 Console |
| 下拉键盘 | ArrowDown 打开并聚焦 option；Enter 选择；Escape 关闭；焦点恢复 |
| 下拉语义 | 字段、级别、分类、来源、日志内容、刷新间隔、已保存视图均有上下文名称 |
| 导航状态 | 点击主导航后 `aria-current`、侧栏当前项和 H1 同步 |
| 顶层 Modal | 打开后焦点位于关闭按钮；Shift+Tab 从首项回绕末项；背景 7 个分支 inert；Body 滚动锁定 |
| 嵌套 Modal | 外层事务日志 + 内层归档策略双栈通过；Escape 仅关闭顶层并恢复到归档策略触发器 |
| Modal 完全关闭 | dialog 0、inert 0、Body overflow 恢复、焦点回到事务日志入口 |
| 日志交互行 | 240/240 行可聚焦；首行 Enter、次行 Space 均更新唯一 `aria-current` 并打开日志详情 |
| 行内控件隔离 | 模型测试确认嵌套按钮触发的 Enter 不会冒泡为整行选择 |
| 数据库分页 | 真实浏览器识别具名 `navigation`；空数据为 `status`；第 1/1 页时前后页均禁用 |
| 平台事件分页 | 真实浏览器识别具名 `navigation`；状态播报“第 1 / 1 页，共 0 条”；空态可播报 |
| 时间模式切换 | 真实浏览器识别为具名原生按钮；点击后相对/精确模式与可访问名称同步 |
| 桌面 1280×800 | 页面 clientWidth = scrollWidth = 1280 |
| 平板 820×900 | 页面 clientWidth = scrollWidth = 820；侧栏转换为横向页面导航 |
| 手机 390×844 | 页面 clientWidth = scrollWidth = 390；顶部/侧边/主内容纵向重排 |
| 浏览器运行日志 | 0 error / 0 warning |

## 5. 尚未完成

1. 当前业务表格尚无已定义的可排序列；后续如引入排序，需统一 `aria-sort`、键盘触发和服务端排序契约。分页与空态基础契约已完成，仍缺组件级 DOM 测试。
2. 接入 axe、颜色对比度检测与 Playwright 核心业务流，形成 CI 门禁。
3. 将详情 Drawer 进一步收敛为带独立焦点契约的通用组件。
4. 继续拆分 `MonitorOverview.vue` 等超大组件，减少样式与交互状态耦合。
5. 按 ADR-011 完成 Debug Web、独立 KB Admin 与统一 Console 的迁移/退役边界。
