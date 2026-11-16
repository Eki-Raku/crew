# Changelog

## Unreleased

- 规范统一调配子智能体命名格式为 `[模型名]角色名`（如 `[gpt-6.1-sol]实施者` / `[gpt-6.1-sol]implementer`）：内置角色随 `--lang` 语言设置显示中文或英文角色名，自定义角色保留角色 ID；底层 `agent_type` 保持不变。同步输出至 `preflight.subagent_names`、SessionStart 摘要、`dispatch_input.name`、派发消息头、`workflow report`、`SubagentStart` 上下文与 `coordinator` 预设提示词。
- 新增角色配置字段 `max_instances`（正整数，默认 `1`），用于设置同一类型角色最多允许同时创建并行的实例数量；支持通过 `role create/update --max-instances`、`presets install --max-instances`、向导与 TUI 管理，并在 `preflight.max_instances`、`SessionStart` 摘要与 `workflow dispatch --count` 中生效。工作流支持一次派发多个并行槽位（命名自动编号 `#1`、`#2`…），各自独立关联 `spawn_agent`、编辑身份校验与 Start/Stop 生命周期，全部停止后统一交付与审查。

## 0.4.2

- 回执文件损坏或被手改时，`record`/`doctor` 视为空回执，不再让 Hook 抛异常。
- Git 探测失败（如 safe.directory）且处于 Git 检出内时，拒绝回退为目录作用域，避免同一项目上下文分叉；非 Git 目录行为不变。
- `context open` 对近期已刷新且无变化的会话走只读路径，减少并行子 agent 的写锁竞争；决策摘要只查 key/version。
- 新增 `context prune`（默认预览，`--apply` 执行）：仅清理长期静默的子 agent 会话链接与已删除任务的会话链接，任务、状态和决策不受影响。
- 工作流 Hook 在 PreToolUse/PostToolUse 遇到无关工具时直接返回，不再打开 Git/SQLite；配置只读取一次。
- Stop 纠正不再递增工作流版本，避免协调者刚读取状态就遇到版本冲突。
- `workflow deliver` 把 Git 报告但未被登记的改动写入 `delivery.unlisted_changes`（仅供审查参考，不阻塞；可能包含任务前已有的改动）。
- 产物路径对 `.git` 的拒绝改为大小写无关；工作流表增加 `workflow_meta` schema 记录；`_change` 拆为按动作分发的方法。
- `setup` 只保留最近 5 份 AGENTS 备份；`doctor` 检查 AGENTS 条目里的 CLI 路径是否仍存在；原子写入同步父目录；Claude 导出转义 U+0085/U+2028/U+2029；移除未使用的 `tomllib` 导入。

## 0.4.1

- 拒绝项目配置、向导、导出目录/文件、锁、共享数据库及 SQLite sidecar 中的符号链接，防止项目操作改写或清理外部/全局数据。配置读写、原子替换和清理使用不跟随链接的目录句柄。
- 角色配置拒绝未知字段，避免 Claude 的 tools、permissionMode、skills 等限制被静默丢弃。已有配置原文不会被自动删改；需要这些字段的 agent 应在 Crew 之外维护。
- Hook 使用共享 2 秒内部预算和 2.5 秒外层时限，缩短数据库与回执等锁，避免超过宿主超时。已有 schema 不再每次重新获取写锁；Git 探测超时不再切换任务作用域。
- --provider 必须与选定 --host 一致；不再把 Claude 会话误存到 Codex 的数据库。手工流程明确传入 --host。
- 界面语言切换保留任务 state/changes 和用户角色内容原文，包括 next。
- Codex TOML 正确转义 DEL 等控制字符，导出前解析验证；Hook 后续命令带正确引用的解释器、插件绝对路径、宿主和项目。
- 增加隔离安全回归、Linux/macOS 与 Python 3.11/3.14 CI，以及备份、升级与回退说明。

这些验证不启动真实宿主或模型，不代表已经验证某个 Codex/Claude Code 版本、模型权限或宿主 Hook 信任设置。数据库路径检查不构成针对同一账户下恶意进程持续并发改动文件的沙箱。
