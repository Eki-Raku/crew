# Crew Cookbook

从本地插件目录开始，配置角色并在会话之间共享任务。需要 macOS 或 Linux，以及 Python 3.11 或更高版本。

## 在宿主中加载 Crew

保留完整的 Crew 目录，并将它的 `bin/` 加入 PATH。下方命令使用 `crew`，也可以用 `python3 /路径/crew/bin/crew` 运行 CLI。

### Codex

如果 Codex 桌面版的插件市场提供 ZIP 导入入口，可选择 Crew 发布包。也可解压后通过本地插件市场安装。按宿主提示启用并信任 Hook。 [OpenAI Docs · 本地插件安装](https://developers.openai.com/plugins/build/plugins#install-a-local-plugin-manually)

预览并安装 Crew 的全局 AGENTS 入口：

```sh
crew --host codex setup
crew --host codex setup --apply
```

### Claude Code

用发布 ZIP 或解压后的插件目录启动一次会话。 [Claude Code CLI 文档](https://code.claude.com/docs/en/cli-reference)

```sh
claude --plugin-dir /路径/crew.zip
# 或使用已解压的目录
claude --plugin-dir /路径/crew
```

也可以登记本地市场后安装：

```sh
claude plugin marketplace add /路径/crew
claude plugin install crew@crew-local
```

## 给项目配置角色

以下以 Codex 为例。配置 Claude Code 时，将命令里的 `--host codex` 改为 `--host claude`。

```sh
cd /路径/项目
crew --host codex --lang zh init --interactive
crew --host codex export
crew --host codex export --apply
```

在向导中选择角色、填写宿主可用的模型 ID、设置思考强度，然后确认配置预览。输入 `q` 或 Ctrl+C 可暂停，重新运行向导即可继续。

`export` 预览角色文件的写入和移除，加 `--apply` 后生效。Codex 写入 `.codex/agents/`，Claude Code 写入 `.claude/agents/`。若在 Claude Code 会话中首次创建 agents 目录，导出后重启会话再使用新角色。

## 维护角色与提示词

```sh
crew --host codex presets list
crew --host codex role list
crew --host codex role show reviewer
crew --host codex role update reviewer --instructions-file ./reviewer.md
crew --host codex role update reviewer --effort high
crew --host codex export
crew --host codex export --apply
```

用 `role create` 新建角色，或用 `role disable` 和 `role enable` 控制角色启用状态。项目配置可覆盖全局角色。用 `--scope global` 管理全局配置，两个宿主的角色设置各自维护。需要切换 CLI 语言时，使用 `--lang zh` 或 `--lang en`。

## 在会话之间接续同一任务

以下命令以同一个 Codex 项目中的两个会话为例。用实际会话 ID 替换 `FIRST_SESSION_ID` 和 `SECOND_SESSION_ID`，用第一条命令返回的任务 ID 替换 `TASK_ID`。

```sh
crew --host codex context open --session FIRST_SESSION_ID
crew --host codex context open --session SECOND_SESSION_ID --task TASK_ID
crew --host codex context read --task TASK_ID
```

读取完整状态后，记录返回的版本。下一次可用 `--since LAST_VERSION` 读取新增、变更和移除的字段。写入时带上当前版本和唯一请求 ID。遇到版本冲突时，重新读取状态并合并后再提交。

```sh
crew --host codex context patch --task TASK_ID --expected-version CURRENT_VERSION \
  --file patch.json --request-id UNIQUE_WRITE_ID
```

在 `patch.json` 中写入需要更新的结构化字段，例如 `{"next":["审阅接口变更"]}`。用 `context decisions --scope project` 查看长期项目决定。

## 排查配置或导出问题

```sh
crew --host codex preflight
crew --host codex doctor
crew --host codex hook status
```

先用 `preflight` 确认配置和导出状态，再用 `doctor` 检查宿主、入口与 Hook。用 `hook status` 查看已有的事件回执。
