"""Pre-turn injection for Codex and Claude Code; no transcript reads or lifecycle guessing."""
import json
import hashlib
from pathlib import Path
import shlex
import sys
import time
from . import core as c
from . import hosts

INJECT = {'SessionStart', 'UserPromptSubmit', 'SubagentStart'}


def context_command(project, *arguments):
    return shlex.join([sys.executable, str(Path(__file__).resolve().parents[1] / 'bin' / 'crew'),
                       '--host', hosts.CURRENT, 'context', '--project',
                       str(Path(project).expanduser().resolve()), *arguments])


def handle(event, project, *, deadline=None):
    if deadline is None:
        deadline = time.monotonic() + 2.0
    name = event.get('hook_event_name')
    if name not in INJECT:
        # SessionEnd also means shutdown/idle. Compaction/end are diagnostic only.
        return {}
    if not c.effective(project)['enabled']: return {}
    sid = event.get('session_id')
    if not isinstance(sid, str) or not sid.strip() or len(sid) > 256:
        return {'hookSpecificOutput': {'hookEventName': name, 'additionalContext': 'Crew: session identity unavailable; shared context was not associated. Use ' + context_command(project, 'open', '--session', '<session-id>') + ' with an explicit session ID.'}}
    from .context_store import ContextStore
    try:
        store = ContextStore(project, deadline=deadline, busy_timeout=0.1)
        provider = hosts.CURRENT  # Sessions are keyed by host; each host also has its own store.
        opened = store.open_session(provider, sid)
        agent_id = event.get('agent_id')
        if isinstance(agent_id, str) and agent_id:
            if len(agent_id) > 128: raise c.Error('Invalid agent ID')
            child_key = sid + '/agent/' + agent_id
            if len(child_key) > 256:
                child_key = 'agent:' + hashlib.sha256(json.dumps([sid, agent_id]).encode()).hexdigest()
            opened = store.open_session(provider, child_key, task_id=opened['task_id'])
        # A compact header on ordinary turns prevents repeated full snapshots.
        # SessionStart (including compact/resume) receives a bounded orientation.
        packet = {'host': provider, 'task_id': opened['task_id'], 'version': opened['version'],
                  'reset': opened.get('reset', False), 'created': opened.get('created', False)}
        state = opened.get('state', {})
        if name == 'SessionStart' or packet['reset'] or packet['created']:
            packet['orientation'] = {k: state[k] for k in ('summary', 'goal', 'next', 'blockers') if k in state}
            # Never silently imply an abbreviated orientation is the full checkpoint.
            while len(json.dumps(packet, ensure_ascii=False)) > 2400 and packet.get('orientation'):
                packet['orientation'].pop(next(reversed(packet['orientation'])))
            packet['omitted_fields'] = sorted(set(state) - set(packet.get('orientation', {})))
        # Long-lived decisions stay separately scoped and versioned. Send metadata,
        # not the entire decisions text every turn; skill instructs an initial read.
        for scope in ('global', 'project'):
            decisions = store.decision_list(scope=scope)
            packet[scope + '_decisions'] = decisions_metadata(decisions)
        command = context_command(project, 'read', '--task', opened['task_id'])
        instruction = 'Read initial state and scoped decisions once; later use read --since <known-version>. Before continuing, replace any cached task state if task_id changed. Save concise changes with patch and expected-version after milestones.'
        if packet['reset']:
            instruction = 'The previous record was removed. This is a fresh empty record. Discard cached shared state for the old task; do not reconstruct it from transcript/checkpoints. Record only newly established facts. ' + instruction
        return {'hookSpecificOutput': {'hookEventName': name, 'additionalContext': 'Crew ' + json.dumps(packet, ensure_ascii=False, separators=(',', ':')) + '\n' + command + '\n' + instruction}}
    except c.Error as e:
        return {'hookSpecificOutput': {'hookEventName': name, 'additionalContext': 'Crew shared context unavailable: ' + str(e)[:500] + '. Preserve current work; inspect ' + context_command(project, 'stats') + '. No lifecycle deletion was inferred.'}}


def decisions_metadata(data):
    # A digest tells the agent when to refresh scoped decisions without repeating them.
    if isinstance(data, dict): rows = data.get('decisions', data.get('items', []))
    else: rows = data if isinstance(data, list) else []
    if isinstance(rows, dict): versions = sorted((k, v.get('version')) for k, v in rows.items() if isinstance(v, dict))
    elif isinstance(rows, list): versions = sorted((r.get('key'), r.get('version')) for r in rows if isinstance(r, dict))
    else: versions = []
    return {'count': len(versions), 'digest': hashlib.sha256(json.dumps(versions, sort_keys=True).encode()).hexdigest()[:12]}
