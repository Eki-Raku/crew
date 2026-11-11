"""UI translations. JSON keys/status values and command names remain stable."""
import os
import re
LANG = 'en'

def set_language(language=None):
    global LANG
    language = language or os.environ.get('CREW_LANG') or os.environ.get('ROLEPILOT_LANG') or os.environ.get('LC_ALL') or os.environ.get('LANG', 'en')
    LANG = 'zh' if language.lower().startswith('zh') else 'en'
    return LANG

ZH = {
    'UI language': '界面语言',
    'UI language (also CREW_LANG)': '界面语言（也可设置 CREW_LANG）',
    'Built-in prompt language (defaults to UI language)': '内置提示词语言（默认跟随界面）',
    'usage: ': '用法：',
    'the following arguments are required: %s': '缺少必需参数：%s',
    'unrecognized arguments: %s': '无法识别的参数：%s',
    'invalid choice: %(value)r (choose from %(choices)s)': '无效选项：%(value)r（可选 %(choices)s）',
    'argument %(argument_name)s: %(message)s': '参数 %(argument_name)s：%(message)s',
    'expected one argument': '需要一个参数值',
    'not allowed with argument %s': '不能与参数 %s 一起使用',
    'Crew: local roles, setup and hook diagnostics; no model calls': 'Crew：角色配置、入口设置与 Hook 诊断',
    'Machine JSON (default for non-TUI commands)': 'JSON 输出（非 TUI 命令默认格式）',
    'UTF-8 prompt file; empty file clears the prompt': 'UTF-8 提示词文件；空文件会清空提示词',
    'Confirm delete (reveals inherited global role); disable is usually safer': '确认删除（可能恢复全局继承角色）；也可选择停用',
    'JSON file with schema_version, roles and optional enabled; absent returns questions': '含 schema_version、roles 及可选 enabled 的 JSON 文件；省略则返回初始化问题',
    'Which roles do you need?': '需要哪些角色？',
    'Choose an available model ID from your Codex model picker.': '从 Codex 模型列表中选择可用的模型 ID。',
    'Choose an effort supported by that model.': '选择该模型支持的思考强度。',
    'Choose model and effort when installing; editable afterwards.': '导入时选择模型与思考强度，之后可修改。',
    'Review/edit prompts and model choices, then preview export.': '检查并编辑提示词与模型配置，然后预览导出。',
    'Preview export, then export --apply to synchronize host role files.': '预览 export，然后执行 export --apply 同步宿主角色文件。',
    'Preview export, then export --apply to synchronize project role files.': '预览 export，然后执行 export --apply 同步项目角色文件。',
    'Configure roles, then export them. This command does not install or enable the host plugin.': '配置角色后导出。宿主插件的安装与启用在宿主中完成。',
    'Use role update for existing roles; export --apply after reviewing export preview': '使用 role update 修改已有角色；检查导出预览后执行 export --apply',
    'Config, receipts and exported agents are retained; remove exports using export --clean --apply.': '配置、回执与角色导出文件已保留；可用 export --clean --apply 清理导出。',
    'Host enablement and model entitlement require the actual Codex client.': '插件启用与模型可用性需要在实际 Codex 客户端验证。',
    'A receipt is historical evidence, not proof the hook is still enabled.': '回执记录历史事件；当前 Hook 启用状态请在宿主中查看。',
    'Role changes affect future agents, not running agents.': '角色变更在新建 agent 时生效。',
    'Project disablement does not remove global agent exports; prefer project-only export.': '项目停用后仍需单独处理全局导出；推荐按项目导出。',
    'Role name required': '请提供角色名', 'Role not found': '未找到角色',
    'Answers file not found': '未找到初始化答案文件',
    'Preset install requires --model and --effort from your host model picker': '导入模板需要提供 --model 与 --effort，请以宿主模型列表为准',
    'Role already exists; use update': '角色已存在，请使用 update',
    'No role in this scope; use create for an override': '当前作用域没有该角色；可使用 create 创建覆盖配置',
    'No role in this scope to enable': '当前作用域没有可恢复的角色',
    'Role definition missing; update its model, effort, description and instructions before enabling': '缺少角色定义；请先补齐模型、强度、描述和提示词，再启用',
    'Role not present in this scope; use disable to mask an inherited role': '当前作用域没有该角色；使用 disable 可停用继承的角色',
    'Use --apply to delete; inherited global role may reappear. Use disable to mask it.': '删除需加 --apply，可能恢复全局继承角色。使用 disable 可停用继承角色。',
    'TUI requires a terminal; use init --json for host-guided questions': 'TUI 需要交互终端；宿主问答请使用 init --json',
    'Role must be an object': '角色配置必须为对象', 'enabled must be boolean': 'enabled 必须为布尔值',
    'disabled must be boolean': 'disabled 必须为布尔值',
    'instructions must be a string; use an empty string for an empty agent': 'instructions 必须为字符串；空 agent 使用空字符串',
    'Role id must match [a-z][a-z0-9_-]{0,47}': '角色 ID 格式应为 [a-z][a-z0-9_-]{0,47}',
    'Invalid model identifier': '无效的模型标识', 'Invalid cwd': '无效的工作目录',
    'Hook input too large': 'Hook 输入过大', 'Hook input must be JSON object': 'Hook 输入必须为 JSON 对象',
    'Malformed Crew marker; refusing to edit': 'Crew 标记不完整，请修复后再编辑',
    'Invalid action': '无效操作', 'Invalid prompt action': '无效的提示词操作',
    '\n╭─ Crew · interactive terminal ─╮': '\n╭─ Crew · 交互终端 ─╮',
    'Start with 10 for guided setup, or 2 for a custom/empty agent.': '输入 10 开始逐项初始化，或输入 2 创建自定义 / 空 agent。',
    '1 Roles  2 Create/update  3 Enable/disable/delete  4 Setup entry\n5 Export agents  6 Doctor / Hook ping  7 Scope  8 Enable/disable  9 Built-in roles  10 Guided setup  11 Shared context  12 Switch host  0 Exit': '1 查看角色  2 创建/修改  3 恢复/停用/删除角色  4 设置入口\n5 导出角色  6 诊断 / Hook 自测  7 切换作用域  8 启停插件  9 内置角色  10 初始化向导  11 共享上下文  12 切换宿主  0 退出',
    'Select': '请选择', 'Role id': '角色 ID', 'Model ID from your Codex picker': 'Codex 模型列表中的模型 ID',
    'Supported effort': '支持的思考强度', 'Description': '描述', 'Save? y/N': '保存？y/N',
    'Action: enable, disable or delete': '操作：enable（恢复）、disable（停用）或 delete（删除）',
    'Confirm? y/N': '确认？y/N', 'Apply global entry? y/N': '写入全局入口？y/N', 'Apply export? y/N': '应用导出？y/N',
    'Run simulated Hook ping? y/N': '运行 Hook 模拟自测？y/N', 'Enable? y/N': '启用？y/N',
    'Role ids separated by commas; empty = all': '角色 ID 用逗号分隔；留空选择全部',
    'Import missing roles? Existing roles are preserved. y/N': '导入缺失角色？已有角色将保留。y/N',
    'Prompt action': '提示词操作', 'Prompt file': '提示词文件',
    'Enter prompt; finish with a single . line. Immediate . creates an empty agent.': '输入多行提示词，以单独一行 . 结束；直接输入 . 创建空 agent。',
    '1 Write multiline prompt  2 Load UTF-8 file  3 Clear (empty agent)': '1 输入多行提示词  2 读取 UTF-8 文件  3 清空（空 agent）',
    '1 Write multiline prompt  2 Load UTF-8 file  3 Clear (empty agent)  4 Keep current': '1 输入多行提示词  2 读取 UTF-8 文件  3 清空（空 agent）  4 保留当前提示词',
    '[empty agent / 空 agent]': '[空 agent]',
}
ZH.update({
    'JSON value answering the current step': '当前步骤的 JSON 答案值',
    'JSON array of model IDs supplied by the host or user': '宿主或用户提供的模型 ID 列表 JSON 文件',
    'Choose the roles to add.': '选择要添加的角色。',
    'Choose a model ID available in your host.': '选择宿主中可用的模型 ID。',
    'Save this configuration?': '保存这份配置？',
    'Configuration saved. Preview export next.': '配置已保存，接下来预览导出。',
    'Host may render choices or ask them as text. CLI cannot require a native selector.': '宿主可使用原生选择器或文字问答展示步骤；CLI 提供结构化数据。',
    'Select (comma-separated for multiple roles; q to pause)': '请选择（多角色用逗号分隔，q 暂停）',
    'Select a number, or enter a model ID. Prefix a numeric model ID with =.': '选择编号或输入模型 ID；纯数字模型 ID 可加 = 前缀。',
    'Draft saved. Resume with init --interactive.': '草稿已保存，使用 init --interactive 继续。',
    'Reset requires --apply; saved role configuration is retained': '重置草稿需加 --apply；已保存的角色配置会保留',
    'Unsupported onboarding draft': '不支持的初始化草稿版本',
    'Invalid onboarding draft fields': '初始化草稿字段无效',
    'Invalid onboarding selection': '初始化角色选择无效',
    'Invalid onboarding role data': '初始化角色数据无效',
    'Models must be a JSON array of nonempty model IDs': '模型列表必须是非空模型 ID 字符串组成的 JSON 数组',
    'Update model choices separately before answering': '请先单独更新模型选项，再提交答案',
    'Stale answer; read init again for the current step and revision': '答案已过期；请重新执行 init 获取当前步骤与版本号',
    'Choose one or more unique role IDs from the listed choices': '请从选项中选择一个或多个不重复的角色 ID',
    'Select a listed effort value': '请选择列出的思考强度',
    'Confirmation must be true or false': '确认值必须为 true 或 false',
    'Confirmation requires --apply': '保存确认需要 --apply',
    'Configuration changed or confirmation token missing; review init preview again': '配置已变化或缺少确认令牌；请重新查看 init 预览',
    'Initialization is complete; use --reset --apply to start another draft': '初始化已完成；使用 --reset --apply 开始新草稿',
    'Use either --interactive or --answer': '--interactive 与 --answer 请二选一',
    'Answer cannot be null': '答案不能为 null',
    'Review migrated Crew configuration and export it using crew export, then crew export --apply.': '检查迁移后的 Crew 配置，先执行 crew export 预览，再用 crew export --apply 导出。',
    'Disable the old RolePilot plugin in the host before enabling Crew hooks, to avoid duplicate events.': '启用 Crew Hook 前，在宿主停用旧 RolePilot 插件，避免重复事件。',
    'Using the old RolePilot CLI, preview rolepilot export --clean --project <project>; apply with --apply after checking the listed files. Repeat with --scope global if global exports are listed.': '使用旧 CLI 执行 rolepilot export --clean --project <project> 预览；检查文件后加 --apply 清理。若有全局导出，另加 --scope global 重复操作。',
    'Using the old RolePilot CLI, preview rolepilot uninstall; apply with --apply to remove only its marked AGENTS blocks. Then preview crew setup and apply the new entry.': '使用旧 CLI 预览 rolepilot uninstall，再加 --apply 移除旧标记块；然后预览 crew setup 并写入新入口。',
    'Keep legacy configuration and receipts as a backup. Migration does not delete or move them.': '保留旧配置与回执作为备份；迁移采用复制合并。',
})

PREFIXES = {
    'Current prompt: ': '当前提示词：', 'Effort options: ': '思考强度选项：',
    'Error: ': '错误：', 'Missing role field: ': '缺少角色字段：',
    'Unknown presets: ': '未知模板：', 'Invalid hook JSON: ': '无效的 Hook JSON：',
    'Invalid roles in ': '角色配置无效：', 'Invalid existing agent TOML: ': '已有角色 TOML 无效：',
    'Agent name collision: ': '角色名冲突：', 'Unmanaged file collision: ': '与其他文件冲突：',
    'effort must be one of ': 'effort 可选值：',
    'Cannot read ': '无法读取：', 'Busy or stale lock: ': '文件锁正忙或已过期：',
    'Unsupported configuration schema: ': '不支持的配置版本：',
}

def f(template, **values):
    """Translate a message template, then fill its {placeholders}."""
    return t(template).format(**values)

def t(value):
    if LANG != 'zh' or not isinstance(value, str): return value
    if value in ZH: return ZH[value]
    if value.startswith('Project: ') and '\nScope: ' in value:
        return value.replace('Project: ', '项目：', 1).replace('\nScope: ', '\n作用域：', 1)
    for prefix, translated in PREFIXES.items():
        if value.startswith(prefix): return translated + t(value[len(prefix):])
    return value

# Translate human-facing fields only; role prompts, paths and identifiers are data.
TEXT_FIELDS = {'next_steps', 'presentation', 'message', 'note', 'next', 'question', 'model_selection', 'limitations'}
def localize(value, key=None):
    if isinstance(value, dict): return {k: localize(v, k) for k, v in value.items()}
    if isinstance(value, list): return [localize(v, key) for v in value]
    return t(value) if key in TEXT_FIELDS else value

ZH.update({
    '\nCrew shared context': '\nCrew 共享上下文',
    '1 List  2 Open/link  3 Read  4 Patch\n5 Checkpoint  6 Archive/delete/restore  7 Session state\n8 Decisions  9 Capacity  0 Back': '1 列表  2 创建/关联  3 读取  4 更新\n5 检查点  6 归档/删除/恢复  7 会话状态\n8 长期决定  9 容量  0 返回',
    'Provider (codex/claude)': '平台（codex/claude）',
    'Session ID': '会话 ID', 'Task ID': '任务记录 ID', 'Current task ID': '当前任务记录 ID',
    'Shared task ID (empty creates/resumes)': '共享任务 ID（留空创建或继续）',
    'Title': '标题', 'Since version (empty = snapshot)': '从哪个版本增量读取（留空读快照）',
    'Expected version': '预期版本号', 'JSON file': 'JSON 文件',
    'archive/delete/restore': 'archive（归档）/delete（移入回收区）/restore（恢复）',
    'active/archived/deleted/unknown': 'active（活跃）/archived（归档）/deleted（已删除）/unknown（未知）',
    'global/project': 'global（用户全局）/project（当前项目）',
    'list/set/delete': 'list（查看）/set（设置）/delete（删除）',
    'Decision key': '决定标识', 'Decision text': '决定内容',
    'Apply safe maintenance? y/N': '执行安全维护？y/N',
    'Review the task ID, then repeat with --apply.': '检查任务记录 ID 后，加 --apply 执行。',
    'UTF-8 JSON state patch or checkpoint': 'UTF-8 JSON 状态更新或检查点文件',
    'Stable idempotency key for a retried write': '重试写入使用的稳定幂等标识',
})
PREFIXES['Missing argument: '] = '缺少参数：'

ZH.update({
    'list/set/delete/restore': 'list（查看）/set（设置）/delete（删除）/restore（恢复）',
    'Version must be a nonnegative integer': '版本号必须为非负整数',
    'Task not found in this project scope': '当前项目未找到该任务记录',
    'Task was deleted; open the continuing session for fresh context, or explicitly restore the old task': '任务记录已删除；继续会话将关联新记录，也可显式恢复旧记录',
    'request_id was already used with different content or version': 'request_id 已被其他内容或版本使用',
    'Decision scope must be project or global': '决定的作用域必须为 project 或 global',
    'Decision not found in this scope': '当前作用域未找到该决定',
    'Session association changed; stale lifecycle observation rejected': '会话关联已变化，过期状态提交已拒绝',
    'Session not found in this project scope; open it before recording status': '当前项目未找到该会话，请先关联再记录状态',
    'Session status must be active, unknown, archived, or deleted': '会话状态必须为 active、unknown、archived 或 deleted',
})
PREFIXES.update({'Context version conflict: ': '上下文版本冲突：',
    'Context must be an object containing only: ': '上下文必须为对象，仅支持以下字段：',
    'Cannot initialize context database: ': '无法初始化上下文数据库：',
    'Context database operation failed: ': '上下文数据库操作失败：'})

# Host selection (Codex / Claude Code)
ZH.update({
    'Target host; defaults to CREW_HOST, then the detected environment, then codex': '目标宿主；默认依次取 CREW_HOST、检测到的环境、codex',
    'Session provider (defaults to the selected host)': '会话所属平台（默认取当前宿主）',
    'Host: ': '宿主：',
    'Claude Code model ID, alias or inherit': 'Claude Code 模型 ID、别名或 inherit',
    'Choose a Claude Code model: a full model ID, an alias such as sonnet or opus, or inherit.': '选择 Claude Code 模型：完整模型 ID、sonnet / opus 等别名，或 inherit。',
    'Plugin enablement and model access require the actual Claude Code client.': '插件启用与模型可用性需要在实际 Claude Code 客户端验证。',
    'Claude Code needs no global entry: the SessionStart hook already delivers the Crew status each session, and CLAUDE.md is left untouched.': 'Claude Code 不需要全局入口：SessionStart Hook 每次会话都会提供 Crew 状态，CLAUDE.md 保持原样。',
    'This agents directory is new. Claude Code only watches agent directories that existed when the session started; restart the session to load these roles.': '这是新建的 agents 目录。Claude Code 只监听会话启动时已存在的 agents 目录；请重启会话以加载这些角色。',
    'RolePilot migration applies to Codex only; rerun with --host codex': 'RolePilot 迁移仅适用于 Codex；请加 --host codex 重新执行',
    'Role has no settings for any host': '角色没有任何宿主的配置',
    'hosts must map a supported host (claude) to its role settings; Codex settings stay at the top level': 'hosts 只能包含受支持的宿主（claude）及其角色配置；Codex 配置保留在顶层',
    'Crew {version}: {label} environment detected ({evidence}).': 'Crew {version}：检测到 {label} 环境（{evidence}）。',
    'Crew {version}: running as {label} ({source}); no environment marker was found.': 'Crew {version}：按 {label} 运行（来源 {source}）；未发现环境标记。',
    'Using {label} roles and tasks only: {count} role(s), status {status}.': '仅使用 {label} 的角色与任务：{count} 个角色，状态 {status}。',
    '{count} role(s) are configured for the other host only.': '另有 {count} 个角色仅为另一宿主配置。',
    'Crew: the {declared} hooks file was loaded inside {actual} ({evidence}). Nothing was read or written. Use hooks/hooks.json for Claude Code and hooks/codex.json for Codex.': 'Crew：在 {actual} 中加载了 {declared} 的 Hook 文件（{evidence}）。未读写任何数据。Claude Code 使用 hooks/hooks.json，Codex 使用 hooks/codex.json。',
    'Crew hook skipped: ': 'Crew Hook 已跳过：',
})
PREFIXES.update({'Invalid hosts section in ': 'hosts 配置无效：', 'Crew hook skipped: ': 'Crew Hook 已跳过：'})
TEXT_FIELDS.add('systemMessage')
