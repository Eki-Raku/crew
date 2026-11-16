# 持续分工工作流

工程任务采用：**协调者确认目标与验收 → 实施 agent → 独立审查 agent → 协调者验收**。

状态保存在原有共享上下文 SQLite 中。工作流版本单独管理，写入使用 CAS；会话恢复、压缩后继续原任务。模型和思考强度沿用用户配置。

## 使用

```sh
crew workflow start --session '<宿主真实 session_id>' --goal '实现目标' --acceptance '验收要求' --implementer implementer --reviewer reviewer
crew workflow read --task '<task_id>'
crew workflow dispatch --task '<task_id>' --expected-version 1 [--count 1]
```

协调者把返回的 `dispatch_input`（并行时为 `dispatch_inputs` 列表）的 `agent_type`、`task_name`、`message` 原样用于宿主 `spawn_agent`——名字由 `workflow dispatch` 一次性算好并存入状态，Hook 会拦截不一致、缺少标记或自拟 `task_name` 的创建（见下方「命名约束」）。可在 message 后追加代码位置、相关资料和互不重叠的子任务范围。`--count` 受角色配置中的 `max_instances`（默认 `1`）限制，工作量较大时可按上限同时派发多个同类实例并行执行，并在名称后附加 `#1`、`#2` 区分。`dispatch_input.name`（及 `preflight.subagent_names`、`workflow report` 中的 `subagent_name`）按 `【模型名】角色名` 规范生成统一显示名（内置角色随 `--lang` 显示中英文，自定义角色保留角色 ID），底层 `agent_type`（`crew_<role>` / `crew-<role>`）保持不变以兼容宿主标识校验。Hook 通过显式标记和 tool_use_id 关联派发，再用成功返回的 agent_id 匹配生命周期事件。生命周期事件提前到达时暂存并按精确 ID 关联。

实施返回后，读取版本，再登记交付：

```sh
crew workflow deliver --task ID --expected-version N --files src/main.py tests/test_main.py --summary '实现与改动说明' --tests '实际执行的命令及结果'
crew workflow dispatch --task ID --expected-version N
crew workflow review --task ID --expected-version N --verdict pass --summary '检查范围、发现与验证空白'
crew workflow finish --task ID --expected-version N
```

每一步的 `N` 均使用最近一次读取返回的版本。派发审查的消息包含验收标准、交付文件指纹和测试证据。所有实施者与审查者必须是不同的实际 agent。审查后声明的产物变化会阻止完成。

- `retry --reason '...'`：作废本次派发或交付并回到可重派状态。重派前先处理仍在运行的旧 agent。
- `pause --reason '等待授权'` / `resume --reason '已获得授权'`：等待与恢复。
- `cancel --reason '用户取消'`：取消当前工作流。宿主 agent 的终止由协调者调用宿主工具。
- `next --goal '下一个目标' --acceptance '验收要求'`：完成或取消后在同一会话开始新一轮。
- TUI 的 `13 Engineering workflow` 可进行上述管理。

## 命名约束（Codex）

依据 Codex 0.169.0 实际行为（二进制中的校验与线程库记录），而非推测：

| 名称 | 谁决定 | 约束 |
|---|---|---|
| `task_name`（面板里的 `/root/<task_name>`） | 调用 `spawn_agent` 的协调者 | 仅小写字母、数字、下划线；不得与仍打开的同名 agent 重复 |
| 原生昵称 `nickname_candidates` | Codex 在候选中挑选，调用者无法指定 | 仅 ASCII 字母、数字、空格、连字符、下划线；非法值会让整个角色文件无法加载 |

因此 `[ ]`、`【 】`、`.` 与中文都**不能**进入 Codex 原生昵称或 `task_name`。Crew 的做法：

1. `workflow dispatch` 一次性算出并保存每个槽位的 `display_name`（`【模型名】角色名`，并行时加 `#i`）、`task_name`（如 `gpt_6_1_sol_implementer`、并行 `_1`、重派 `_r2`）与原生昵称候选；之后的输出、报告与校验只读取这些已存值。
2. `export` 为 Codex 角色写入 `nickname_candidates`（如 `gpt-6-1-sol implementer`，并行时带序号），使原生界面名称受控。
3. PreToolUse 对工作流角色的 `spawn_agent` 强制比对：`agent_type`、`task_name`、派发标记、模型与强度必须与存档一致；缺少标记同样拒绝并给出应使用的值。
4. `workflow report` 的 `native_nickname_observed` 记录 Codex 实际返回的昵称，便于核对。

`workflow` 之外临时创建的角色（如直接点名 `crew_tester`）不在此约束内。

## 生效范围

| 机制 | 实现范围 |
|---|---|
| 派发检查 | 当前 token、调用 ID、导出角色名和成功 agent_id 匹配 |
| 交付检查 | 实施 Start/Stop 回执、交付说明和测试证据、明确列出的文件指纹 |
| 独立审查 | 审查者与本轮所有实施者不同，审查绑定当前交付文件 |
| 编辑入口 | Codex `apply_patch`：首次修改要求开启工作流；默认 identity 模式要求当前已启动且未停止的实施 agent 身份匹配 |
| 结束纠偏 | 相同未完成状态提醒一次；等待、取消、重复纠偏放行 |
| 恢复 | 当前共享任务、阶段、派发记录继续使用 |

Shell/MCP 写入未纳入编辑门槛。新工作流默认采用 identity 校验：PreToolUse 必须提供与当前实施派发一致的 agent_id、agent_type。缺字段只判为身份无法核验，不猜测它是主控还是旧版执行者。旧宿主可显式使用 `workflow start --edit-policy stage`；已开始的任务可先取消，再用 `workflow next --edit-policy stage` 开启兼容轮次。`workflow read` 会显示实际 edit_protection，stage 保留阶段校验；宿主有身份字段时仍拒绝错误实施者，缺字段时按阶段放行。既有未记录策略的开发版工作流保留 stage 行为。产物指纹覆盖显式声明的路径，未声明文件需由独立审查确认是否遗漏；`deliver` 会把 Git 报告但未登记的改动记入 `delivery.unlisted_changes` 供审查者核对（仅作提示，可能含任务前已有的改动）。测试证据和审查结论由 agent 报告，Hook 证明的是派发与生命周期关联。

Hook 需宿主版本支持并信任插件；故障按既有策略放行，工作流不会自动补造回执。`host_enforcement: not_verified` 提醒需要真实客户端验收。CLI 模拟事件测试不能替代客户端测试。Claude 的既有角色/共享上下文管理保持原样，本工作流的自动回执适配先用于 Codex。

## 验证依据

- [Codex Hooks](https://learn.chatgpt.com/docs/hooks)：事件输入、输出、工具覆盖与失败行为。
- [Codex spawn_agent](https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/handlers/multi_agents/spawn.rs)：成功结果中的 agent_id。
- 自动化测试：`python -m unittest discover -s tests -v`；工作流回归位于 `tests/test_workflow.py`。

## 第 2 项：协调者抢活的证据与取舍

与持续分工共用同一工作流，不再建立第二套调度器。

| 路线 | 证据与适用性 | 处理 |
|---|---|---|
| 仅反复提示协调职责 | 已有模板和阶段提醒；模型仍可绕开职责自行编辑 | 保留作恢复引导 |
| 在已支持工具上验证实施者身份 | 最新源码的 PreToolUse 可提供子 agent ID/类型；与真实派发记录匹配 | 本轮采用，默认 identity |
| 另建执行器或改变主线程沙箱 | 涉及宿主控制与兼容部署，超过现有插件的可靠权限边界 | 当前不引入 |

固定源码证据（`f1ebc7b372bc837b7c6386113e867256d7cc7036`）：

- [session.rs](https://github.com/openai/codex/blob/f1ebc7b372bc837b7c6386113e867256d7cc7036/codex-rs/core/src/session/session.rs#L637-L640)：父子线程共享 session_id。
- [hook_runtime.rs](https://github.com/openai/codex/blob/f1ebc7b372bc837b7c6386113e867256d7cc7036/codex-rs/core/src/hook_runtime.rs#L198-L210)：PreToolUse 使用子线程上下文；[身份构造](https://github.com/openai/codex/blob/f1ebc7b372bc837b7c6386113e867256d7cc7036/codex-rs/core/src/hook_runtime.rs#L1047-L1053)采用子线程 ID 和角色。
- [schema.rs](https://github.com/openai/codex/blob/f1ebc7b372bc837b7c6386113e867256d7cc7036/codex-rs/hooks/src/schema.rs#L279-L286)：空 agent 身份被省略，因此缺字段不能直接证明是主控。

源码能力与用户当前安装版本分别验证。identity 模式拒绝无法核验的编辑，提示宿主所需字段；stage 模式是显式降级。两种模式都不会宣称已经保护 Shell/MCP 或证明客户端 Hook 已执行。没有读取会话文件，也没有依据 transcript_path 推断身份。

## 第 3～7 项：合并后的闭环

### 3. 长程规则保持

与第 1、2 项共用持久状态。SessionStart（包括 compact/resume）重新提示当前阶段和编辑校验模式；同一派发 token 继续使用。回归测试反复压缩恢复后检查：主控/未知身份仍不能通过 identity 编辑门槛，当前实施者仍能工作，交付后不能继续编辑旧阶段。

### 4. 测试资源收尾

采用**显式资源记录 + 完成门槛**，复用同一 SQLite 和 CAS。三种路线中，纯提示清单无法保留遗留资源；通用自动捕获缺少可靠的宿主事件；显式记录能准确保存责任与处置结果。

[原生工具源码](https://github.com/openai/codex/blob/f1ebc7b372bc837b7c6386113e867256d7cc7036/codex-rs/core/src/tools/context.rs)中，运行中的 exec_command 暂不生成 PostToolUse；返回的终端 session_id 是管理器句柄，不是 OS PID。[write_stdin](https://github.com/openai/codex/blob/f1ebc7b372bc837b7c6386113e867256d7cc7036/codex-rs/core/src/tools/handlers/unified_exec/write_stdin.rs)结束后才可补回原命令的 PostToolUse。浏览器/MCP 各工具的资源字段不同，不能据此写通用 kill 或关闭逻辑。

创建任务专用资源后登记，资源归属必须是协调会话或已关联 agent：

```sh
crew workflow resource-add --task ID --expected-version N --kind process --locator 'terminal:42' --owner AGENT_ID --purpose 'QA server' --creation-evidence 'exec_command returned terminal 42'
crew workflow resource-release --task ID --expected-version N --resource-id RESOURCE_ID --evidence '原工具报告进程已退出'
crew workflow resource-retain --task ID --expected-version N --resource-id RESOURCE_ID --evidence '用户要求保留供验收'
```

kind 支持 process、tab、app、temporary、other。只登记本任务创建的资源，保留原始句柄及创建证据。执行者通过创建资源的原工具进行清理，确认结果后登记释放；用户要求保留时保存要求依据。插件本身不会杀进程、按端口清理、关闭页面或删除文件。

有 open 资源时不能通过审查或完成。取消保留资源记录，仍可补记释放/保留；下一轮开始前也检查未处置项。资源证据标记为 `reported`，它不是插件对操作系统的独立核验。未知或未登记的资源需由执行者和审查者检查，不补造“已清理”。

### 5. 授权等待

保留显式 `pause --reason '等待事项'`；收到明确结果后使用 `resume --reason '恢复依据'`。暂停时 Stop 不催促，其他调用成功也不会自动恢复。

[PermissionRequest 输入构造](https://github.com/openai/codex/blob/f1ebc7b372bc837b7c6386113e867256d7cc7036/codex-rs/hooks/src/events/permission_request.rs)没有向插件传 tool_use_id，而且该事件位于实际审批之前。当前没有可靠的审批结果配对信号。按会话、命令或时间猜测恢复会串联并发请求，因此不增加这种自动化，也不改变权限决定。

### 6. 可核对的交付汇报

`crew workflow report --task ID` 输出精简报告：当前阶段与下一步、真实关联的 agent ID、派发时配置的模型/强度、交付及测试说明、审查结论、资源处置与等待/恢复依据。TUI 也使用该报告。

模型字段明确叫 `configured_model/configured_effort`，不冒充实测 token 用量或宿主实际推理配置。派发与生命周期来自本地 Hook 观察；测试、清理与审查内容来自 agent 报告。完成后仍保留本轮实施/审查身份，开启下一轮才重置本轮工作流。

### 7. 减少人工监督

合并到以上阶段门槛、持久状态、收尾检查和下一步提示。没有添加独立统计、提醒服务或额外模型评判。实际客户端的 Hook 验收仍是发布前的独立步骤。

资源台账由执行者显式登记。取消后仍可补登记迟发现的本任务既有资源；登记不会创建或关闭真实资源。`next` 清空本轮工作流及其中已处置/retained资源记录；它不是跨轮永久资产台账。需长期保留的交付说明应在共享上下文或项目文档中记录。`report` 只汇报当前轮，也不承担 token 统计。

定位信息、创建/清理证据和测试说明请使用短句及文件位置，不要保存密钥、完整带token的URL或整段敏感日志。这些字段保存在本地，并可能进入审查任务包。

## 原生 Codex CLI 的兼容包

在 `codex-cli 0.167.0-alpha.4` 的真实发现测试中，portable `plugin.json` 包能安装并加载技能，但 `hooks/list` 返回空列表。对应源码的 AgentPlugin 分支暂时跳过本地 Hook 加载。

原 App/portable 包保持不变。为该 CLI 构建兼容包：

```sh
python scripts/build_codex_cli.py --output /tmp/crew-codex-cli.zip
```

兼容包把 portable manifest 保存在 `plugin.portable.json`，使用正式的 `.codex-plugin/plugin.json` legacy 入口指向 `hooks/codex.json`。在隔离安装目录中，原生客户端已能发现 9 个 Hook；执行前仍须正常信任这些 Hook。构建不会修改用户配置，也不会安装或信任插件。输出路径必须不存在。
