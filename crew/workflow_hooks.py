"""Codex-only adapter. No identity inference from prompts or timestamps."""
import json
import re
import shlex
import sys
from pathlib import Path
from . import core, hosts, i18n
from .workflow import WorkflowStore, instruction

MARKER = re.compile(r'^\[crew-dispatch:([0-9a-f]{32})\](?:\n|$)')
EVENTS = {'SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'SubagentStart', 'SubagentStop', 'Stop'}


def handle(event, project, deadline=None):
    name = event.get('hook_event_name')
    if hosts.CURRENT != 'codex' or name not in EVENTS: return {}
    sid = event.get('session_id')
    if not isinstance(sid, str) or not sid or len(sid) > 256: return {}
    # Tool events that no gate cares about must not pay for git/SQLite/config work.
    if name in ('PreToolUse', 'PostToolUse') and event.get('tool_name') not in ('apply_patch', 'spawn_agent', 'Agent'): return {}
    cfg = core.effective(project)
    if not cfg['enabled']: return {}
    store = WorkflowStore(project, deadline=deadline, busy_timeout=.1)
    data = store.for_session(sid)
    if not data:
        roles = cfg['roles']
        ready = all(r in roles and not roles[r].get('disabled') for r in ('implementer', 'reviewer'))
        command = shlex.join([sys.executable, str(Path(__file__).resolve().parents[1] / 'bin' / 'crew'),
                              '--host', 'codex', 'workflow', 'start', '--project', str(project), '--session', sid,
                              '--implementer', 'implementer', '--reviewer', 'reviewer'])
        guidance = 'Crew workflow inactive. For engineering work, run ' + command + ' --goal <goal> --acceptance <criteria> before editing. Use configured role IDs if they differ.'
        if i18n.LANG == 'zh':
            guidance = 'Crew 工作流未启用。工程修改前执行 ' + command + ' --goal <目标> --acceptance <验收要求>。自定义角色请换成已配置的角色 ID。'
        if name == 'PreToolUse' and event.get('tool_name') == 'apply_patch' and ready:
            return {'hookSpecificOutput': {'hookEventName': name, 'permissionDecision': 'deny', 'permissionDecisionReason': guidance}}
        if name == 'SessionStart':
            return {'hookSpecificOutput': {'hookEventName': name, 'additionalContext': guidance}}
        return {}
    task = data['task_id']
    if name == 'PreToolUse' and event.get('tool_name') == 'apply_patch':
        # New Codex builds identify child callers. Missing identity can also mean
        # an older client, so reject an unverified writer without naming its role.
        reason = None
        attempt = data.get('attempt')
        if data['phase'] != 'implementing' or not attempt or not attempt['started'] or attempt['stopped']:
            reason = instruction(data) + i18n.t(' Start delegated implementation before editing.')
        else:
            writer = event.get('agent_id')
            writer_role = event.get('agent_type')
            if writer is not None:
                if writer != attempt['agent_id'] or writer_role != hosts.export().agent_name(attempt['role']):
                    reason = i18n.t('This edit is not from the assigned implementation agent. Delegate the change to that agent.')
            elif data.get('edit_policy', 'stage') == 'identity':
                reason = i18n.t('Crew cannot verify this edit caller. Use the assigned implementation agent on a Codex build exposing PreToolUse agent_id and agent_type. Older clients can explicitly select stage-only compatibility for a new workflow cycle.')
        if reason:
            return {'hookSpecificOutput': {'hookEventName': name, 'permissionDecision': 'deny', 'permissionDecisionReason': reason}}
        return {}
    if name in ('PreToolUse', 'PostToolUse'):
        if event.get('tool_name') not in ('spawn_agent', 'Agent'): return {}
        inp = event.get('tool_input')
        if not isinstance(inp, dict): return {}
        message = inp.get('message')
        match = MARKER.match(message) if isinstance(message, str) else None
        token = match.group(1) if match else None
        tool_id = event.get('tool_use_id')
        if not isinstance(tool_id, str) or not 0 < len(tool_id) <= 256: return {}
        role = inp.get('agent_type')
        attempt = data['attempt']
        if name == 'PreToolUse' and token:
            if (not attempt or token != attempt['token'] or role != hosts.export().agent_name(attempt['role'])
                    or data['phase'] != 'dispatch_pending'
                    or attempt['tool_use_id'] not in (None, tool_id)
                    or inp.get('model', attempt['model']) != attempt['model']
                    or inp.get('reasoning_effort', attempt['effort']) != attempt['effort']):
                return {'hookSpecificOutput': {'hookEventName': name, 'permissionDecision': 'deny',
                        'permissionDecisionReason': i18n.t('Crew dispatch does not match the current assignment. Read workflow state and use its dispatch_input.')}}
        agent_id = None
        if name == 'PostToolUse':
            response = event.get('tool_response')
            if isinstance(response, str):
                try: response = json.loads(response)
                except ValueError: response = None
            if isinstance(response, dict) and not response.get('error'):
                candidate = response.get('agent_id')
                if isinstance(candidate, str) and 0 < len(candidate) <= 128: agent_id = candidate
        data = store.observe(task, sid, name, token=token, tool_use_id=tool_id, agent_id=agent_id, role=role)
    elif name in ('SubagentStart', 'SubagentStop'):
        agent_id, role = event.get('agent_id'), event.get('agent_type')
        if not isinstance(agent_id, str) or not 0 < len(agent_id) <= 128 or not isinstance(role, str): return {}
        data = store.observe(task, sid, name, agent_id=agent_id, role=role)
        if name == 'SubagentStop': return {}  # stopping is not acceptance
    elif name == 'Stop':
        if event.get('stop_hook_active') is True: return {}
        reason = store.correction(task)
        return {'decision': 'block', 'reason': reason + i18n.t(' If waiting on approval or external work, pause with a reason.')} if reason else {}
    # Short instructions at actual boundaries, never full shared-state repetition.
    if name == 'SubagentStart':
        info = i18n.t('Crew worker: complete your assigned stage and report files, tests and findings. Coordinator owns workflow transitions.')
    else: info = instruction(data)
    return {'hookSpecificOutput': {'hookEventName': name, 'additionalContext': info}}
