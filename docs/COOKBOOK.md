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

## 支持字段与安全边界

角色目前只支持 `model`、`effort`、`description`、`instructions`、`disabled`。不支持的字段会报错，而不是导出时静默丢弃。例如 Claude 的 `tools`、`permissionMode`、`skills` 并未由 Crew 实现；如果需要这些控制，请在 Crew 之外维护对应 agent。不要为了通过校验而删掉用户要求的权限限制。

项目 `.crew`、`.codex/agents`、`.claude/agents` 及它们的受管理文件不能使用符号链接。全局 `crew` 数据目录、数据库及其 `-wal`、`-shm`、`-journal` 同样拒绝链接。请先备份，再把受管理数据放到普通目录。显式选择的项目根与宿主 home 会规范化为真实路径；显式 CREW_DATA_HOME 只规范化其父目录（兼容 macOS /tmp、/var 别名），数据目录本身仍不能是链接；不要把项目受管理目录链接到全局目录。SQLite 检查会在每次连接前执行，但不声称能隔离同一账户下另一个恶意进程的持续并发改动。

手动使用 Claude 时，所有后续命令都带 `--host claude`，例如 `crew --host claude context open --session SESSION_ID`。`--provider` 默认与所选宿主一致；显式不一致会直接拒绝且不会创建数据库。`doctor` 返回版本与插件根目录，方便检查 PATH 是否指向另一份安装。Hook 提供的后续命令已包含当前 Python、插件绝对路径、宿主和项目，优先使用它。

Hook 的内部共享预算为 2 秒，Unix 主进程外层时限为 2.5 秒，低于清单中最短的 3 秒。数据库繁忙、回执等锁或 Git 探测超时会返回非阻塞提示，不推断任务已归档或删除。该预算不等于对操作系统不可中断 I/O 的硬实时保证。

## 备份、升级与回退

1. 先停止旧版本的 Hook 写入，例如关闭使用它的宿主会话。备份项目 `.crew`、项目导出的 `crew-*` 文件，以及各宿主 home 下的 `crew` 目录。Codex home 默认 `~/.codex`，可由 `CODEX_HOME` 改写；Claude 默认 `~/.claude`，可由 `CLAUDE_CONFIG_DIR` 改写；`CREW_DATA_HOME` 若设置，数据库在该目录。复制 SQLite 时应停止所有写入并连同现存 sidecar 一起备份，或使用 SQLite 在线备份 API，不要在写入中只拷贝主文件。
2. 用新源码目录或新发行包替换插件。保留旧目录以便回退。Git 源码修复不自动更新已有 release ZIP；以实际版本为准。
3. 对每个使用的宿主运行 `--host HOST doctor`、`--host HOST export`；检查目标、增删文件和未知字段报错，再执行 `--host HOST export --apply`。全局导出需要另外明确 `--scope global`，不会从项目复制覆盖值。
4. 如果之前为 Codex 执行过 `setup --apply` 且插件路径变了，重新预览 `setup` 再 `setup --apply`，更新全局入口中的绝对路径。Claude 不需要该入口。重新开启宿主会话，使新 Hook 和首次创建的 agents 目录生效。
5. 回退时停止新 Hook，换回保留的旧插件，再从备份恢复配置/导出；0.4.1 不改变 SQLite schema。回退也会恢复旧版本的已知问题，不建议继续处理不可信项目。`uninstall --apply` 仅移除 Crew 标记入口，不删除配置、任务或导出；`export --clean --apply` 只清理当前作用域的受管理导出。

## 独立验证

```sh
python3 -m compileall -q crew
python3 -m unittest discover -s tests -v
```

测试使用临时配置、临时数据库和模拟 Hook，不启动 Codex 或 Claude Code。CI 矩阵为 Linux/macOS、Python 3.11/3.14，并检查插件清单版本和发行归档结构。真实宿主加载、Hook 信任、模型可用性和 UI 仍须单独验收；当前不声明已验证的宿主版本。
