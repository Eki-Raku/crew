# 原生 Codex 短流程验证（2026-11-16）

客户端：`codex-cli 0.167.0-alpha.4`。仅在独立配置目录和临时 Git 项目中测试；主控和两个计划中的角色均固定 `gpt-6.1-sol / low`，另外安装测试派发保护。测试包装器记录事件并拒绝其他模型派发，随后把原始事件转交实际 Crew Hook。没有测试长程能力、压缩恢复或其他模型。

## 实际完成

1. 原生 `model/list` 确认可用的 Sol 与 low 强度。
2. 安装原 portable 包后，原生 `hooks/list` 和 `plugin/read` 均显示没有 Hooks。当前原生插件加载器对 AgentPlugin 格式跳过本地 Hooks。
3. 新增 `scripts/build_codex_cli.py`，构建采用 `.codex-plugin/plugin.json` 的兼容包，原 App 包不变。实际生成的 ZIP 经原生安装后发现 9 个 Hooks，且没有加载错误。
4. 在隔离配置目录正常记录这 9 个 Hook 的信任；原生状态变为 trusted。没有使用绕过信任或绕过沙箱参数。
5. 发起两个小型 Sol 回合（第二次通过正式执行权限机制重试同一任务）。两次均真实触发 SessionStart、UserPromptSubmit、PreToolUse、Stop、SessionEnd。
6. 两次主控 `apply_patch` 都被 Crew 的工作流入口门槛拒绝，测试文件没有被创建。

## 环境阻塞，未完成的项目

随后原生命令执行报错：

```text
error building bubblewrap command: app-server socket directory must be a user-owned directory with mode 0700
```

官方 `uds/src/daemon_directory.rs` 固定使用 `/tmp/codex-daemon-UID`，并有意不受 HOME、TMPDIR、CODEX_HOME 影响。当前外层环境将该目录屏蔽为不可访问状态；正式执行权限重试仍在相同环境边界中。没有改变共享目录权限，也没有降低 Codex 的 workspace-write 沙箱。

两个回合都停止了，均未开始工作流或派发子 agent。因此下列项仍未通过原生全链路验收：成功派发与 Start/Stop 关联、执行者编辑身份放行、实施交付、独立审查和最终完成。不能把本次入口验证写成完整协作流程通过。

## 证据范围与收尾

实际 Hook 记录中的模型值只有 `gpt-6.1-sol`；子 agent 身份记录为零。两次共观察到 SessionStart 2、UserPromptSubmit 2、PreToolUse 6、Stop 2、SessionEnd 2。

原始日志留在测试执行环境，摘要位于 `native-codex-smoke.json`。认证临时链接已移除，未修改用户原有项目和配置。本次没有发布或推送。

后续应在允许原生 Codex 正常创建其 workspace-write 沙箱的独立环境完成同一个短流程，不需要长程测试。

## 独立云环境复核

另行创建独立 Default 云容器，诊断任务固定 Sol / low。普通命令执行正常；不调用嵌套模型的原生沙箱预检使用本版接受的命名权限配置：

```sh
codex sandbox --permission-profile crew-check \
  -c 'permissions.crew-check.filesystem={ ":root"="read", "/workspace"="write", ":slash_tmp"="write", ":tmpdir"="write" }' \
  -c 'permissions.crew-check.network.enabled=false' \
  -C /workspace -- /bin/true
```

配置解析成功，随后仍以同一 socket directory `0700` 错误退出，`/bin/true` 未执行。独立云容器与 Dot 云电脑均存在这一受管执行边界，因此重复创建同类容器无法完成原生客户端全链路验证。没有修改目录权限、放宽沙箱或访问用户本地 Codex。后续需要允许原生客户端正常使用自身沙箱的独立远端，或平台提供兼容入口。

### 直接使用云主会话的替代路线

已检查独立云主会话的能力。它能读取 Crew 技能，但没有安装、信任和重载原生插件 Hooks 的受支持入口。[官方插件文档](https://learn.chatgpt.com/docs/plugins) 与 [Hook 文档](https://learn.chatgpt.com/docs/hooks#hooks-with-cloud-orchestration) 说明云编排不运行插件命令 Hooks；企业 Global MCP Hooks 也不是本插件的本地命令 Hook。因此在该主会话手动调用 Python 不能替代本次原生验收。

### 外部环境验收条件与步骤

所需环境：用户授权的独立 Linux 远端或测试 VM，Python 3.11+、Git、支持上述 Hooks 的官方 Codex CLI、可用 Sol 登录；原生沙箱能够自行创建并使用其目录。无需用户日常项目，保持 workspace-write 和网络策略；不放宽已有沙箱。费用型环境须另获授权。

1. 在该环境先执行上面的 `codex sandbox ... /bin/true` 预检。失败则停止模型测试。
2. 建立全新 `CODEX_HOME` 与临时 Git 项目，通过正式登录获得测试会话访问，不复制或打印凭证。
3. 使用本仓库 `scripts/build_codex_cli.py` 生成兼容包，按本版 `codex plugin marketplace add --help` 和 `codex plugin add --help` 注册临时本地 marketplace 并安装 `crew@crew-smoke`。在 `/hooks` 审查并信任 9 个 Crew Hooks，核实真实加载。
4. 将主控、review_model、implementer、reviewer 全部显式设为 `gpt-6.1-sol` / `low`。导出原生角色并检查 `crew_implementer`、`crew_reviewer`；测试派发保护拒绝其他模型。
5. 一个短任务先由主控尝试 `apply_patch` 新建 `smoke.txt`，确认被拒绝；随后通过 workflow start/dispatch 交给实施者，目标为文件精确包含 `CREW_OK` 和换行。
6. 实施者实际编辑并运行 Python 内容断言后停止；主控记录交付，派发另一个 Sol reviewer 只读验证，再记录 review pass 和 finish。
7. 核对真实事件中的派发标记、实施者/审查者 ID、Start/Stop、编辑身份放行、断言结果和 completed 状态。缺失事件立即报告，不补写模拟证据。完成后终止测试进程，报告临时资源，不进行长程、压缩或其他模型测试。
