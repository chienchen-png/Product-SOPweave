# Agent 运行时与工具

本项目使用 OpenAI Agents SDK 作为唯一 Agent 运行时。`agent_runtime` 负责流式执行、工具审批、交互中断、checkpoint、停止和恢复，`agent_runtime/tools/catalog.py` 是工具参数与权限策略的唯一来源。

## 运行边界

- 统一 ASGI 入口：`asgi:app`。
- FastAPI 处理 `/api/agent/*` 与 SSE；Flask 提供业务 API 和前端静态资源。
- Agent 定义：`agent_profiles/<agent-id>/agent.yaml` 与 `instructions.md`。
- Skill/SOP 定义：`skills/<skill-id>/SKILL.md`，可包含 `references/`、`assets/` 和受控脚本。
- 持久状态：`data/app.db` 中的 `runs`、`run_events`、`agent_session_items`、`agent_run_checkpoints` 与 `interactions`。
- 内置应用工作区：`data/app-workspaces/*`；用户项目目录由用户选择。

## 工具目录

当前共有 22 个公共工具。默认仅开放 8 个基础工具，其他工具由 Skill 或可信任务声明后按需激活。

| 工具组 | 工具 | 用途 |
| --- | --- | --- |
| core | `list_files`、`search_text`、`read_text`、`write_text`、`edit_text`、`run_process` | 文件、文本和本机程序 |
| skills | `read_skill`、`prepare_skill_draft` | 加载与创作 SOP |
| interaction | `ask_user` | 结构化问题、选择和确认 |
| office | `inspect_workbook`、`read_sheet_range`、`edit_workbook`、`read_document`、`edit_document` | Excel 与 Word |
| project-data | `list_datasets`、`import_dataset`、`query_dataset`、`apply_data_changes`、`export_dataset` | 项目数据库 |
| analysis | `analyze_dataset`、`reconcile_variables`、`generate_chart` | 财务分析、核对和图表 |

工具名称、参数 schema、风险和所属工具组都由 `ToolCatalog` 生成。Agent 配置页和项目对话页读取相同目录，不在前端另写工具清单。

## Agent 与 SOP

平台只维护一个通用 Agent。具体业务流程通过 SOP（Skill）扩展：

- `agent.yaml` 声明允许的 `toolsets`、内置 `skills`、`progressive_tools`、`max_turns` 和审批策略。
- `SKILL.md` 的 YAML 头声明 ID、描述和工具组。
- 正文写业务步骤、输入、约束和输出要求。
- `read_skill` 加载正文后才启用该 Skill 获准的领域工具。
- 每次运行固定实际使用的 SOP 版本，避免中途修改影响当前任务。

## 工具策略

- 文件工具限制在项目授权工作目录内。
- 文本和 Office 编辑需要 `version`；版本冲突时重新读取。
- 写操作使用同目录临时文件和原子替换。
- 数据修改用 `revision` 检测冲突，并记录单元格变更日志。
- `run_process` 使用本地 Python/Shell，在执行前始终请求用户确认。
- 被拒绝的操作不能通过命令、代码或其他工具绕过。
- SOP 可以增加更严格的工具和参数限制，不能降低平台的安全边界。

## 工作模式

| 操作 | 人工审批 | 全自动 |
| --- | --- | --- |
| 读取文件和数据库 | 直接执行 | 直接执行 |
| 项目写入或数据修改 | 用户确认 | 自动执行 |
| 本机命令 | 用户确认 | 用户确认 |
| SOP 强制规则 | 生效 | 生效 |

全自动模式只省略普通项目写操作的确认，不绕过本地命令和 SOP 规则。

## Office、文件与数据

- 文件参数相对项目工作目录解析，不能跳出授权目录。
- Excel 先检查结构，再按 sheet 和 A1 范围读取；保留空格位、重复表头、公式缓存和数字格式。
- Word 按 `block_id` 精确替换，尽量保留原 run 格式；复杂域、修订和嵌入对象需要单独验证。
- `list_datasets` 默认返回紧凑目录。指定 `file` 后读取字段，指定 `file+sheet` 时只读取单表字段。
- `query_dataset` 返回 revision；`apply_data_changes` 使用同一 revision 执行事务修改。
- `export_dataset` 支持精确写回和模板重建；写回前检查源文件版本。

## 程序执行

`run_process` 接受：

- `program`：`python` 或 `shell`
- `code`
- `reason`
- `timeout`
- `output_limit`
- 可选 `artifact_paths`

输出通过 `tool.output` 实时发送，进度通过 `tool.progress` 发送，大结果写入 `.agent-artifacts` 供后续读取。

本机执行始终需要人工确认。需要隔离时，可配置经过验收的容器执行器：

```powershell
$env:SOPWEAVE_EXECUTOR = "docker"
$env:SOPWEAVE_EXECUTOR_IMAGE = "<deployment-specific Python worker image>"
```

## 流式事件与恢复

启动运行：

```text
POST /api/agent/runs
Content-Type: application/json
Accept: text/event-stream
```

服务端先写入递增 `seq` 的事件，再发送给客户端。断线后的恢复流程：

1. `GET /api/agent/runs/{run_id}` 查询状态与 `last_seq`。
2. `GET /api/agent/runs/{run_id}/events?after_seq=...` 补取事件。
3. `GET /api/agent/runs/{run_id}/stream?after_seq=...` 继续订阅。
4. 前端按 `seq` 去重。
5. 工作台运行使用 `background: true`，离开页面不会取消任务。

审批、提问和恢复使用持久化 interaction ID、version 与 checkpoint。重复提交不会重复执行工具；服务重启后，已开始但不可确认的副作用操作标记为中断，交用户检查。

## 模型与供应商

模型配置使用两个稳定标识：

- `models.id`：项目引用使用的数据库 ID。
- `models.api_model`：发送给供应商的真实模型名。

内置 DeepSeek、智谱 GLM、硅基流动适配模板，另有自定义 OpenAI-compatible 配置。模型验证包含五项：

1. 普通文本
2. SSE 流式文本
3. 工具调用
4. 工具结果续接
5. usage 统计

五项均通过才进入聊天和应用的可选模型列表。配置页面只返回 `has_api_key`，不会返回密钥。

供应商地址、协议和思考参数规则在适配器中集中处理。普通 Agent 从模型配置读取生成参数；两个集成应用使用统一 Direct Model Client，并强制关闭思考以加快报告生成。

## 扩展方式

新增工时时，在对应模块声明：

- Pydantic 输入模型
- 执行函数
- 工具组
- 风险级别
- 超时
- 返回版本或 revision

然后把工具加入 `ToolCatalog`，同步更新 Agent/SOP 说明和回归测试。不要在前端或 Skill 正文复制第二份参数契约。

## 验证

```powershell
.\.runtime\Scripts\python.exe -m unittest discover -s tests -t . -p "test_*.py"
powershell -ExecutionPolicy Bypass -File scripts\verify_agent_runtime.ps1
.\.runtime\Scripts\python.exe scripts\check_tool_portability.py
```

真实供应商检查只在显式提供环境变量时执行，结果中不得保存密钥、请求头或供应商原始响应正文。
