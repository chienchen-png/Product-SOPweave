# 架构说明

支持目标：Windows x64、CPython 3.14；前端开发使用 Node.js 24 或 25。完整版本由 `requirements.lock` 与 `frontend/package-lock.json` 锁定。

## 请求与数据流

```text
React 页面 / hooks → lib/api 领域客户端 → HTTP / SSE
    ├─ agent_runtime：Agents SDK、模型适配、工具、交互、运行恢复
    └─ routes：输入验证 → services：核对 / 建模 / 图表 / 报告
                               ↓
               tools 业务内核 / storage / workspace 原子写入
                               ↓
                    SQLite / 用户目录 / 隐藏应用工作区
```

## 代码职责

| 位置 | 职责 |
| --- | --- |
| `launcher`、`scripts/serve.py` | Windows 启动器、单实例与就绪检查、浏览器入口、托盘和所属服务进程树的退出管理 |
| `asgi.py`、`app.py` | FastAPI 生命周期与 SSE、Flask 业务路由装配 |
| `agent_runtime/models` | DeepSeek、GLM、硅基流动、兼容供应商配置与能力验证 |
| `agent_runtime/definitions`、`agent_profiles`、`skills` | Markdown 定义注册、校验、按需加载 |
| `agent_runtime/tools/catalog.py` | 公共工具唯一目录；默认 8 个、按需工具共计 22 个 |
| `agent_runtime/service.py`、`continuations.py` | 运行流、停止、持久待执行意图、单一执行所有者 |
| `routes/{recon,finmod,app_charts,app_reports}.py` | 兼容原 URL，校验命令并转换服务结果 |
| `services` | 核对、字段映射、建模、图表、报告、Office 导出 |
| `tools` | 工作簿扫描/导入、共享 DataFrame、财务计算与核对内核 |
| `storage` | 元数据、SDK 会话、运行事件、报告索引、独立任务库、快照决策 |
| `frontend/src/lib/api` | 按领域划分的请求与类型；`api.ts` 保留统一出口 |
| `frontend/src/hooks` | 隐藏工作区初始化、流程状态；页面按需加载 |
| `scripts`、`tests` | 安装检查、介质生成、发布验收、回归测试 |

## 运行约束

- 两个集成应用各用独立隐藏工作区，不依赖普通项目。
- 普通项目对话不提供 Agent 选择器；前端自动使用注册表中首个启用 Agent 的稳定 ID，平台唯一 Agent 在“设置 → 智能体配置”中查看和编辑。
- SQLite 保存结构化数据；JSON 用于接口和字段描述，不复制第二份业务事实。
- 文件编辑以版本摘要校验并原子替换；并发修改返回 409。
- 待执行请求可恢复；进程死亡时已开始的副作用操作标为中断，交给用户检查。
- 导入任务与核对决策持久化；快照默认保留，可设置保留天数。
- `SOPWEAVE_DATA_DIR` 配置运行数据位置；相对位置相对于应用目录。预发布版本的环境变量只由集中兼容层读取，用户选定的外部工作目录保留原路径。

## 相关文档

[开发指南](../../CONTRIBUTING.md) · [Agent 运行时](AGENT_RUNTIME.md) · [支持与数据流](SUPPORT.md) · [用户指南](USER_GUIDE.md)

## 财务 SOP 工作台

- React Router 管理 `/projects/:id`、`/settings/{models|agent}`、`/sops` 与两个独立应用 URL。旧 `/settings/skills`、`/ai-config` 和 `/tasks` 路由已删除。
- 财务 SOP 页面以统一页面标题栏和管理工具栏承载查看、编辑、删除、导入和新增；对话页单独决定本轮使用的 SOP。管理页不提供全局启用/停用操作，旧停用数据仍由后端状态兼容。
- 普通启动默认启用用户 SOP；`SOPWEAVE_USER_SKILLS_ENABLED=0` 仅保留为隔离测试或运维关闭手段。SOP 管理页不发起项目跳转，调用选择只存在于项目对话 composer。
- `SkillConfigPanel` 以“内置 SOP / 用户 SOP”顶层子导航区分只读目录和用户创作目录；`SkillWorkflowCanvas` 只消费内置步骤数据，按编号、连线和分支渲染静态流程图，用户 SOP 继续使用结构化文字详情。
- `workspace_drafts` 以项目和类型隔离，使用 revision 比较保存；导入数据 revision 改变时失效下游结果。浏览器即时备份不作为执行事实。
- `storage/workbench_store.py` 查询既有 `runs/run_events`，为项目对话和集成应用内部的任务恢复、审批续接及报告读取提供数据；不建立第二套运行库，也不提供独立的全站任务页面。
- `agent_runtime/background.py` 持有后台 SDK 运行；页面通过持久事件 seq 订阅，离页不取消，恢复不重新发起模型请求。
- 项目对话发送时提交 `sop_mode`、`skill_id` 和 `sop_inputs`；`SkillRuntime` 为每个 run 保存授权目录、显式绑定版本和后续加载快照，因此同一项目的不同任务可以使用不同 SOP。
- `ReportWorkspace` 复用 `FileEditor`：核对和建模报告均可编辑、保存和导出；确定性明细与报告文字分离。
- 历史浏览器缓存通过显式导入保留为“旧记录待复核”；从报告列表移除会保留原文件。


## 模型用量与统计首页

- `services/model_usage.py` 持有每个数据库的采集器。`token_usage.call_id` 唯一；开始写 pending，结束仅结算一次，进程启动将旧 pending 标为 interrupted。数据库短事务与模型网络等待分开。
- SDK 通过公开 `UsageRunHooks` + 单次 `ModelResponse.raw_usage` 采集；`DirectModelClient.complete/stream` 共用同一归一化及账本。累计 `usage.updated` 事件仅供运行展示，不作为入账事实。
- 供应商／模型使用稳定 ID 和名称快照，未保存连接测试使用确定性临时身份；新增向导提前分配正式 ID。原始用量只保留计数白名单。
- `services/dashboard.py` 按 IANA 时区生成 UTC 日窗口，一次只读事务聚合账本、runs 和 app_sessions。部分统计失败返回 null 及 quality 状态。日、供应商、模型在 SQL 中聚合，不增加汇总表。
- `GET /api/dashboard/summary?days=7|30&timezone=Asia/Shanghai` 是首页唯一数据接口。旧 `/api/stats` 和 `/api/workbench/summary` 及其专用读法已移除；任务、报告、草稿接口保留各自职责。
- 前端图表只注册必要的 ECharts 组件，持续观察容器宽度、卸载销毁；30 秒刷新在后台标签暂停，范围切换取消旧请求。
- 首页本机时钟使用浏览器 `Date` 本地时区，每秒在前端更新；不写入统计接口或服务端数据。
