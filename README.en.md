# Crew

**A small local tool for multi-model role splitting and quota saving in Codex and Claude Code.**

Bind coordination, implementation, review, research, and testing roles to specific models, reasoning efforts, and prompts, export them as native host subagents, and use a local SQLite store so handoffs don't resend full conversation context. Written in pure Python standard library with zero third-party dependencies and released under the [MIT License](LICENSE).

![MIT License](https://img.shields.io/badge/License-MIT-2563eb)
![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-2563eb)
![Stdlib Only](https://img.shields.io/badge/Deps-Stdlib%20Only-16a34a)
![macOS and Linux](https://img.shields.io/badge/OS-macOS%20%7C%20Linux-475569)
![Codex and Claude Code](https://img.shields.io/badge/Hosts-Codex%20%7C%20Claude%20Code-475569)

[简体中文](README.md) · **English** · [Cookbook (简体中文)](docs/COOKBOOK.md)

## 1. Why I Built It

As a heavy daily user of generative AI coding tools, I burn through roughly 300M to 1B tokens a day across `1x GPT-Pro + 1x GPT-Plus + 1x Claude-Pro` subscriptions.
Previously, I wrote model routing rules into my system prompts so `Codex.app` would pick the right model per task type and collaborate automatically.
After running that setup for a while, three problems stood out:
1. **The main model kept doing everything itself**: Even when the system prompt explicitly defined its job as coordination, it would still jump in and do the implementation work itself, burning expensive flagship quota unnecessarily.
2. **Inter-agent communication turned into bureaucracy**: In long-running tasks, necessary coordination between conversations degenerated into endless context forwarding and seeking sign-off from the main model, wasting huge amounts of tokens in transit.
3. **Native Codex context sharing relies purely on message passing**: My personal best practice is splitting development across role-specific agents, but Codex's native handoff mechanism spends a massive chunk of quota just passing messages back and forth.

Those three issues showed up starkly in my weekly usage dashboard: the flagship `gpt-6-astra` still took **79.6%** of total usage, while `gpt-6.1-sol` was only **11.5%** and `gpt-6.1-terra` was **8.4%**. In one complex design rework session, `gpt-6-astra` (at 100% high reasoning effort) accounted for **92%** of usage and **ate 23.97% of my weekly plan limit in a single task**.

To fix those exact three pain points, I built Crew around two changes:
- **Hard-bind roles and models as native subagents**: Instead of hoping system prompts persuade the main model to delegate, I lock the main session and backend implementation to `gpt-6.1-sol`, assign test runs and environment checks to an operator role on `gpt-6.1-terra`, and reserve `gpt-6-astra` strictly for the architect role doing independent reviews.
- **Replace bureaucratic message passing with a local SQLite delta store**: All roles read and write a shared local task record (goal, constraints, decisions, progress, and evidence)—reading a full snapshot once and pulling only version deltas (`--since`) after that, eliminating repetitive status reporting through the main model.

### Cost Comparison on a Full-Stack Task: ~76.1% Saved

Below is the actual token log captured via local hooks during a full-stack feature delivery (100 backend database regressions, 1,576 frontend tests, independent architecture and UI review, and live concurrency verification)—464 requests and **83.44M tokens** in total:

![Pricing the same requests at Astra rates gives US$103.74. Using the models assigned by Crew role gives US$24.80, about 76% lower.](docs/assets/model-cost-en.svg)

| Role | Agent ID | Model | Requests | Input Tokens (Cached) | Output Tokens (Reasoning) | Token Share | Est. API Cost |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| Main Session (Coordination) | Main Session | `gpt-6.1-sol` | 164 | 25,828,289 (25.47M cached) | 38,767 | 31.0% | \$3.79 |
| Backend Dev (Implementation) | `crew_backend` | `gpt-6.1-sol` | 108 | 20,486,514 (20.15M cached) | 45,348 | 24.6% | \$3.00 |
| Architect (Independent Review) | `crew_architect` | `gpt-6-astra` | 65 | 11,744,815 (11.35M cached) | 16,849 | 14.1% | \$16.16 |
| Operator (Services & QA) | `crew_operator` | `gpt-6.1-terra` | 127 | 25,247,190 (24.82M cached) | 33,455 | 30.3% | \$1.84 |
| **Total (With Crew)** | **4 Roles** | **Astra + Sol + Terra** | **464** | **83,306,808 (98.2% cached)** | **134,419** | **100%** | **\$24.80** |
| *Baseline (If All Astra)* | *Unrouted* | *Priced at `gpt-6-astra`* | *464* | *83,306,808 (same cache)* | *134,419* | *100%* | ***\$103.74*** |

Converted using standard [OpenAI API pricing](https://developers.openai.com/api/docs/pricing) per 1M tokens (Uncached Input / Cached Input / Output: Astra `\$10 / \$1 / \$40`, Sol `\$1.25 / \$0.125 / \$5`, Terra `\$0.40 / \$0.04 / \$1.60`):

1. **85.9% of tokens genuinely went to cost-effective models**: With the main session itself running on `Sol` for lightweight coordination, `Sol` (55.6% usage) and `Terra` (30.3% usage) handled over four-fifths of the workload for just **\$8.63 (34.8% of total cost)**.
2. **Flagship tokens stayed where they matter**: `Astra` dropped from **79.6%–92%** down to **14.1%**, focused strictly on 65 high-leverage architectural review requests.
3. **No more burning quota on chat-based handoffs**: Roles sync decisions and evidence via local versioned state with a **98.2%** cache hit rate, bringing the estimated API bill for the exact same requests down from **\$103.74 to \$24.80 (~76.1% lower)**.

![One Codex task used 83.4M tokens at an estimated cost of US$24.80. Astra, Sol, and Terra accounted for 14.1%, 55.6%, and 30.3% of usage and 65.2%, 27.4%, and 7.4% of cost, respectively.](docs/assets/model-usage-en.svg)

## 2. What It Does

1. **Lock models and reasoning effort per role**
   Includes 5 bilingual templates (`coordinator`, `implementer`, `reviewer`, `researcher`, `tester`), plus custom roles (`backend`, `architect`, `operator`). Pick a model ID and reasoning effort for each role, preview the diff, and export to `.codex/agents/` or `.claude/agents/`.
2. **Avoid resending full context on handoffs**
   Crew stores a small versioned task state in local SQLite (goal, constraints, decisions, progress, next steps, and evidence). Subagents read the full snapshot once and fetch only version deltas (`--since <version>`) on later turns. Across a 30-round handoff benchmark, reading deltas cuts serialized state traffic by **~76.3% (`118.9 KB → 28.2 KB`)**, with compare-and-swap versioning so parallel roles don't overwrite each other.
3. **Works with both Codex and Claude Code**
   One CLI and terminal UI manages both hosts while keeping each host's roles and task database separate. Pure Python 3.11+ standard library, offline, no credentials collected, and no chat transcripts stored.

## 3. Supported Platforms and Terminal UI

| Host | OS | Local setup | Export directory |
| --- | --- | --- | --- |
| Codex | macOS, Linux | Add the Crew directory or import the ZIP as a local plugin | `.codex/agents/crew-*.toml` |
| Claude Code | macOS, Linux | Start with `--plugin-dir` or register a local marketplace | `.claude/agents/crew-*.md` |

Requires Python 3.11+. Switch CLI and template languages anytime with `--lang en` or `--lang zh`.

If you prefer not to memorize flags, run `crew tui` to open the terminal menu for role management, guided setup, exports, and shared task state:

![Crew terminal menu showing role management, guided setup, agent export, and shared context.](docs/assets/crew-terminal-en.png)

## 4. Quick Start

### 1. Import and Initialize in Codex.app (Recommended)

If you use the `Codex.app` desktop client, you don't need to memorize any CLI flags—just upload the plugin ZIP and let the built-in onboarding skill walk you through setup right in chat:

1. **Open Plugins**: Download `crew.zip` from [GitHub Releases](https://github.com/Eki-Raku/crew/releases/latest) and click the **Plugins** icon in the `Codex.app` left sidebar.

   ![Click the Plugins entry in the Codex.app left sidebar.](docs/assets/codex-step-1-entry.png)

2. **Upload plugin archive**: Click **Add** in the top-right corner and select **Upload plugin archive** (`上传插件压缩包`).

   ![Click Add and select Upload plugin archive.](docs/assets/codex-step-2-menu.png)

3. **Select `crew.zip`**: Choose the downloaded `crew.zip` file and click **Add plugin** (`添加插件`).

   ![Select crew.zip and click Add plugin.](docs/assets/codex-step-3-upload.png)

4. **Install the plugin**: On the `crew` detail page, click **+ Install plugin** (`+ 安装插件`, and allow hooks if prompted).

   ![Click Install plugin on the crew detail page.](docs/assets/codex-step-4-install.png)

5. **Launch guided setup**: When the ready dialog appears, click **Set up crew** (`设置 crew`).

   ![Click Set up crew in the ready modal.](docs/assets/codex-step-5-ready.png)

6. **Configure roles interactively in chat**: This pre-fills the chat box with the `crew` onboarding skill prompt. Select your project folder and send the message—Codex will ask which roles, models, and reasoning efforts you want, show a preview, and export the agents to `.codex/agents/`.

   ![Send the pre-filled crew onboarding skill prompt in chat.](docs/assets/codex-step-6-skill.png)

7. **Call roles by name**: Once exported, delegate tasks directly in chat—for example: *"Spawn `crew_reviewer` to check this change for regressions."*

   ![Codex reviewer role exported by Crew with an English prompt, gpt-6.1-sol, and high reasoning effort.](docs/assets/crew-codex-role-en.png)

### 2. Initialize via Terminal CLI (Codex CLI)

If you prefer setting up from the terminal, run the interactive wizard from your project directory:

```sh
python3 /path/to/crew/bin/crew --host codex --lang en init --interactive
python3 /path/to/crew/bin/crew --host codex export
python3 /path/to/crew/bin/crew --host codex export --apply
```

![Crew setup wizard listing five presets for coordination, implementation, review, research, and testing.](docs/assets/crew-init-en.png)

### 3. Claude Code

1. Start Claude Code in your project directory with `--plugin-dir` pointing to the ZIP or extracted directory ([Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference)):

   ```sh
   claude --plugin-dir /path/to/crew.zip
   ```

2. Configure and export roles from the same directory, then call roles such as `crew-reviewer` in chat:

   ```sh
   python3 /path/to/crew/bin/crew --host claude --lang en init --interactive
   python3 /path/to/crew/bin/crew --host claude export
   python3 /path/to/crew/bin/crew --host claude export --apply
   ```

## 5. My Role Setup for Saving Quota

If you want a similar ~76% cost reduction, here is the three-tier split I use:

| Role Type | Example Roles | Model Tier (GPT Series) | Reasoning Effort | How I Use It |
| --- | --- | --- | --- | --- |
| Architecture & Review | `architect` / `reviewer` | Flagship (`gpt-6-astra`) | `high` / `xhigh` | Called only for design sign-off, tricky bugs, and independent final review |
| Daily Coding | `coordinator` / `implementer` / `backend` | Workhorse (`gpt-6.1-sol`) | `medium` / `high` | Main session scheduling and feature code at **1/8th** the flagship price |
| Verification & Ops | `tester` / `operator` / `researcher` | Fast / Light (`gpt-6.1-terra`) | `low` / `medium` | Starting servers, running test suites, checking UI/DB state, and reading docs at **1/25th** the flagship price |

See the [Cookbook](docs/COOKBOOK.md) for Codex global `AGENTS.md` setup, Claude Code local marketplace installation, custom prompts, and multi-session handoffs.

## 6. FAQ

**Do I need an API key or a Crew account?**

No. Crew only writes local role files and updates a local SQLite task database. It never makes model calls itself; login and billing stay in Codex or Claude Code.

**Can I customize prompts, add my own roles, or use other models?**

Yes. Edit any preset or run `crew role create <name>` to add your own. Any model ID and reasoning effort supported by your host works.

**Do Codex and Claude Code share task state?**

No. Each host keeps its own roles and SQLite database so they never mix. Shared task state is for sessions and subagents within the same host.

**Does Crew change my main session's model automatically?**

No. Exported settings apply to subagents. You still pick the main session model in your host (I usually set my main session to `gpt-6.1-sol` for coordination).

## 7. Safety, Upgrades, and Verification

Crew supports only `model`, `effort`, `description`, `instructions`, and `disabled` role fields. Unsupported fields are rejected, including Claude tool/permission settings Crew does not implement. Keep agents requiring those controls outside Crew; do not remove a restriction merely to make an export pass. Managed configuration/export paths and SQLite files must not be symlinks.

Use explicit `--host` for manual context commands. An explicit `--provider` must match it. Hooks return commands with the active Python and plugin path rather than relying on PATH. `doctor` reports the plugin version and directory.

Before upgrading, stop hook writers and back up each host's Crew directory, project `.crew`, and managed exports. Copy SQLite with all existing sidecars while writers are stopped, or use SQLite's backup API. Preview exports again before applying; rerun Codex `setup` if its installed path changed. Source updates do not update an existing release ZIP. See the [upgrade and rollback guide](docs/COOKBOOK.md#备份升级与回退) and [changelog](CHANGELOG.md).

Run `python3 -m unittest discover -s tests -v` from the plugin directory. CI covers Linux/macOS with Python 3.11/3.14 and validates archive contents. Tests do not launch a host or call models. Actual host loading, hook trust, and model availability require separate verification; no tested host-version baseline is claimed. Path checks do not sandbox another local process continuously racing SQLite's own file opens.
