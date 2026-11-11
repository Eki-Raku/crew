# Crew

**为 Codex 和 Claude Code 配置一支分工明确的 AI 团队。**

为协调、实现、审查等角色分别选择模型、思考强度和提示词。用共享任务状态把决定和下一步交给接手的会话。Crew 是一个本地运行的开源插件，采用 [MIT 许可证](LICENSE)。

![许可证 MIT](https://img.shields.io/badge/%E8%AE%B8%E5%8F%AF%E8%AF%81-MIT-2563eb)
![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-2563eb)
![macOS 和 Linux](https://img.shields.io/badge/%E7%B3%BB%E7%BB%9F-macOS%20%7C%20Linux-475569)
![Codex 和 Claude Code](https://img.shields.io/badge/%E5%AE%BF%E4%B8%BB-Codex%20%7C%20Claude%20Code-475569)

**简体中文** · [English](README.en.md) · [Cookbook](docs/COOKBOOK.md)

## 为什么用 Crew？

- **按职责分配模型。** 协调、实现、审查、研究和测试各有双语预设。给不同角色设置模型和思考强度，预览后再导出到宿主。
- **交接时保留已经确定的事。** 同一宿主内，接手的会话可以读取任务摘要、约束、决定和下一步。首次读取快照，后续按版本读取变化，减少重复传送完整任务状态。
- **在本机管理角色。** 用同一套命令管理 Codex 和 Claude Code，两个宿主各自保存角色与任务状态。Crew 使用 Python 标准库，无需安装第三方依赖。

### 一次协作的 token 用量与费用

![一次 Codex 协作共使用 8,344 万 token，估算费用为 24.80 美元。Astra、Sol、Terra 的用量占比分别为 14.1%、55.6%、30.3%，费用占比分别为 65.2%、27.4%、7.4%。](docs/assets/model-usage-zh.svg)

Sol 和 Terra 合计承担了 **85.9% 的 token 用量**，对应 **34.8% 的估算费用**。

### 全部用 Astra，还是按角色分配模型？

![以同一批请求估算，全部按 Astra 计价为 103.74 美元，按 Crew 角色配置计价为 24.80 美元，低约 76%。](docs/assets/model-cost-zh.svg)

以一次 Codex 开发任务的匿名汇总为例，固定输入、缓存和输出 token 数，分别按全部使用 Astra 和 Crew 实际配置的模型换算。**约 76%** 是两种计价情景的费用差，实际改用 Astra 执行时的用量可能不同。 [OpenAI API 价目表](https://developers.openai.com/api/docs/pricing)

## 支持平台

| 宿主 | 系统 | 本地加载方式 |
| --- | --- | --- |
| Codex | macOS、Linux | 在支持本地插件的 Codex 中添加 Crew 目录 |
| Claude Code | macOS、Linux | 用 `--plugin-dir` 启动，或登记为本地插件市场 |

需要 Python 3.11+。CLI、终端菜单和角色预设支持简体中文与英文，可用 `--lang zh` 或 `--lang en` 切换界面语言。

Crew 的终端菜单（`crew tui`）把角色管理、初始化向导、导出和共享上下文放在一个入口。

![Crew 终端菜单，显示角色管理、初始化向导、角色导出和共享上下文入口。](docs/assets/crew-terminal-zh.png)

## 快速开始

### Codex

1. 从 [GitHub Releases](https://github.com/Eki-Raku/crew/releases/latest) 下载 Crew 插件 ZIP。如果 Codex 桌面版的插件市场提供 ZIP 导入入口，选择该文件。也可将发布包解压，把 Crew 目录加入本地插件市场，然后从插件市场安装。按 Codex 提示启用 Hook。 [OpenAI Docs · 本地插件安装](https://developers.openai.com/plugins/build/plugins#install-a-local-plugin-manually)
2. 在要配置的项目目录运行以下命令。若从 ZIP 安装，可用另行解压的 Crew 目录运行 CLI。

   ```sh
   python3 /路径/crew/bin/crew --host codex --lang zh init --interactive
   python3 /路径/crew/bin/crew --host codex export
   python3 /路径/crew/bin/crew --host codex export --apply
   ```

   ![Crew 中文初始化向导，列出协调、实现、审查、研究和测试五种角色。](docs/assets/crew-init-zh.png)

3. 在 Codex 中调用导出的角色。例如，输入「请启动 `crew_reviewer`，检查这次改动是否引入回归」。

   ![Crew 导出的 Codex 审查角色，带中文描述和提示词，模型为 gpt-6.1-sol，思考强度为 high。](docs/assets/crew-codex-role-zh.png)

### Claude Code

1. 在项目目录启动 Claude Code。用 `--plugin-dir` 指定发布 ZIP 或解压后的插件目录，两种方式都对本次会话生效。 [Claude Code CLI 文档](https://code.claude.com/docs/en/cli-reference)

   ```sh
   claude --plugin-dir /路径/crew.zip
   ```

2. 在同一项目目录用解压后的 Crew CLI 配置并导出角色，然后在 Claude Code 中调用，例如 `crew-reviewer`。

   ```sh
   python3 /路径/crew/bin/crew --host claude --lang zh init --interactive
   python3 /路径/crew/bin/crew --host claude export
   python3 /路径/crew/bin/crew --host claude export --apply
   ```

Codex 全局入口、Claude Code 本地市场安装和多会话交接见 [Cookbook](docs/COOKBOOK.md)。

## FAQ

**需要 API Key 或 Crew 账号吗？**

Crew 在本地配置角色和任务状态。模型的登录、权限与用量由 Codex 或 Claude Code 管理。

**可以用自己的提示词和模型吗？**

可以。可从预设开始，也可以新建角色。模型 ID 和思考强度可按角色配置，模型可用性以宿主为准。

**Codex 和 Claude Code 能读到彼此的任务吗？**

两个宿主各自保存角色与任务状态。共享任务适用于同一宿主内的会话。

**Crew 会自动切换主会话的模型吗？**

角色设置用于导出的子代理。主会话的模型在宿主里选择。
