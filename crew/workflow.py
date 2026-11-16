"""Explicit engineering workflow in the shared-context SQLite database.

The state machine checks evidence, not prose claiming that delegation happened.
Hook receipts are local observations, not a security boundary against the local user.
"""
import hashlib
import json
from pathlib import Path
import uuid

from . import core, hosts, i18n
from .context_store import ContextStore, ConflictError, _identifier, _version

TERMINAL = {'completed', 'cancelled'}


def text(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 4096 or any(ord(ch) < 32 and ch not in '\n\r\t' for ch in value):
        raise core.Error('Workflow text must contain 1 to 4096 characters; only newline and tab controls are allowed')
    return value


def fingerprint(project, files):
    if not isinstance(files, list) or not 1 <= len(files) <= 128:
        raise core.Error('Provide 1 to 128 artifact paths')
    root = Path(project).resolve()
    result = {}
    for name in files:
        _identifier(name, 'artifact path', 512)
        path = Path(name)
        if path.is_absolute() or '..' in path.parts or not path.parts or '.git' in path.parts:
            raise core.Error('Artifact paths must stay inside the project')
        full = root / path
        # Refuse symlinks in every component, including symlinks pointing inward.
        for parent in [full, *full.parents]:
            if parent == root: break
            if parent.is_symlink(): raise core.Error('Artifact symlinks are not supported')
        if not full.exists():
            result[path.as_posix()] = None  # an explicitly listed deleted artifact
            continue
        if not full.is_file() or full.stat().st_size > 16 * 1024 * 1024:
            raise core.Error('Artifact must be a regular file of at most 16 MiB')
        result[path.as_posix()] = hashlib.sha256(full.read_bytes()).hexdigest()
    return result


class WorkflowStore:
    def __init__(self, project, **kwargs):
        self.context = ContextStore(project, **kwargs)
        self.project = project
        with self.context._transaction() as conn:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='workflows'").fetchone()
        if not exists:
            with self.context._transaction(write=True) as conn:
                conn.execute('CREATE TABLE IF NOT EXISTS workflows (task_id TEXT PRIMARY KEY REFERENCES tasks(task_id), version INTEGER NOT NULL, data TEXT NOT NULL)')

    def _get(self, conn, task):
        row = self.context._task(conn, task)
        if row['status'] != 'active': raise core.Error('Workflow context must be active')
        saved = conn.execute('SELECT version,data FROM workflows WHERE task_id=?', (task,)).fetchone()
        if not saved: raise core.Error('Workflow not started')
        return dict(json.loads(saved['data']), version=saved['version'], task_id=task)

    def read(self, task):
        with self.context._transaction() as conn: return self._get(conn, task)

    def for_session(self, session):
        with self.context._transaction() as conn:
            row = conn.execute('SELECT task_id FROM sessions WHERE scope_id=? AND provider=? AND session_id=? AND status=?',
                               (self.context.scope_id, hosts.CURRENT, session, 'active')).fetchone()
            if not row: return None
            try: return self._get(conn, row['task_id'])
            except core.Error: return None

    def start(self, task, session, goal, implementer, reviewer, acceptance=None, edit_policy="identity"):
        if edit_policy not in ('identity', 'stage'): raise core.Error('Invalid edit policy')
        text(goal, 'goal'); text(acceptance or goal, 'acceptance'); _identifier(session, 'session')
        roles = core.effective(self.project)['roles']
        if any(role not in roles or roles[role].get('disabled') for role in (implementer, reviewer)):
            raise core.Error('Workflow roles must be configured and enabled')
        if implementer == reviewer: raise core.Error('Implementation and review require different roles')
        with self.context._transaction(write=True) as conn:
            self.context._task(conn, task)
            link = conn.execute('SELECT task_id FROM sessions WHERE scope_id=? AND provider=? AND session_id=? AND status=?',
                                (self.context.scope_id, hosts.CURRENT, session, 'active')).fetchone()
            if not link or link['task_id'] != task: raise core.Error('Session must be associated with this task')
            if conn.execute('SELECT 1 FROM workflows WHERE task_id=?', (task,)).fetchone():
                raise core.Error('Workflow already exists; read it before continuing')
            data = dict(session=session, edit_policy=edit_policy, goal=goal, acceptance=acceptance or goal, implementer=implementer, reviewer=reviewer,
                        phase='awaiting_assignment', attempt=None, delivery=None, review=None, resources={},
                        implementer_ids=[], correction=None, observed_events=[], lifecycle=[])
            conn.execute('INSERT INTO workflows VALUES(?,1,?)', (task, json.dumps(data)))
        return self.read(task)

    def change(self, task, version, action, **args):
        _version(version)
        with self.context._transaction(write=True) as conn:
            data = self._get(conn, task)
            if data['version'] != version: raise ConflictError(version, data['version'])
            self._change(data, action, args)
            return self._save(conn, data)

    def _save(self, conn, data):
        task = data.pop('task_id'); version = data.pop('version') + 1
        conn.execute('UPDATE workflows SET version=?,data=? WHERE task_id=?', (version, json.dumps(data), task))
        return dict(data, task_id=task, version=version)

    def _change(self, d, action, a):
        phase = d['phase']
        if action in ('resource-add', 'resource-release', 'resource-retain'):
            self._resource(d, action, a)
            return
        if action == 'next':
            if phase not in TERMINAL: raise core.Error('Finish or cancel this workflow before starting the next goal')
            self._clean(d)
            policy = a.get('edit_policy') or d.get('edit_policy', 'stage')
            if policy not in ('identity', 'stage'): raise core.Error('Invalid edit policy')
            d.update(edit_policy=policy, goal=text(a['goal'], 'goal'), acceptance=text(a.get('acceptance') or a['goal'], 'acceptance'), phase='awaiting_assignment', attempt=None,
                     delivery=None, review=None, resources={}, implementer_ids=[], correction=None, lifecycle=[], observed_events=[])
            d.pop('reason', None); d.pop('resume_phase', None); d.pop('previous_review', None); d.pop('resume_evidence', None); d.pop('retry_reason', None)
            return
        if action == 'cancel':
            if phase == 'completed': raise core.Error('Completed workflow cannot be cancelled')
            d.update(phase='cancelled', reason=text(a['reason'], 'reason')); return
        if phase in TERMINAL: raise core.Error('Workflow is closed')
        if action == 'pause':
            if phase == 'blocked': raise core.Error('Workflow is already paused')
            d.update(resume_phase=phase, phase='blocked', reason=text(a['reason'], 'reason')); return
        if action == 'resume':
            if phase != 'blocked': raise core.Error('Workflow is not paused')
            d['resume_evidence'] = text(a.get('reason'), 'resume evidence')
            d['phase'] = d.pop('resume_phase'); d.pop('reason', None); return
        if phase == 'blocked': raise core.Error('Resume the workflow before continuing')
        if action == 'dispatch':
            if phase not in ('awaiting_assignment', 'needs_review', 'changes_needed'):
                raise core.Error('This stage already has an assignment; wait or retry explicitly')
            stage = 'review' if phase == 'needs_review' else 'implementation'
            role = d['reviewer' if stage == 'review' else 'implementer']
            configured = core.effective(self.project)['roles'].get(role)
            if not configured or configured.get('disabled'): raise core.Error('Role is no longer enabled')
            token = uuid.uuid4().hex
            d['lifecycle'] = []
            d.update(attempt=dict(token=token, stage=stage, role=role, model=configured['model'], effort=configured['effort'], agent_id=None, tool_use_id=None,
                                  started=False, stopped=False), phase='dispatch_pending')
            if stage == 'implementation':
                d['previous_review'] = d.get('review') or d.get('previous_review')
                d.update(delivery=None, review=None)
            return
        if action == 'retry':
            if not d['attempt'] and phase not in ('ready', 'needs_review', 'changes_needed'):
                raise core.Error('No assignment to retry')
            text(a['reason'], 'reason')
            # A ready/delivered artifact can be explicitly invalidated and rebuilt.
            d['phase'] = 'needs_review' if d['attempt'] and d['attempt']['stage'] == 'review' else 'changes_needed'
            if d['phase'] == 'needs_review':
                try: self._current(d)
                except (core.Error, OSError): d['phase'] = 'changes_needed'
            if d['phase'] == 'changes_needed':
                d['previous_review'] = d.get('review') or d.get('previous_review')
                d.update(delivery=None, review=None)
            d['attempt'] = None
            d['retry_reason'] = a['reason']
            return
        if action == 'deliver':
            attempt = self._ready(d, 'implementation')
            files = fingerprint(self.project, a['files'])
            d['delivery'] = dict(agent_id=attempt['agent_id'], role=attempt['role'], configured_model=attempt['model'], configured_effort=attempt['effort'], files=files, summary=text(a['summary'], 'summary'), tests=text(a['tests'], 'tests'))
            d.update(phase='needs_review', attempt=None, review=None); return
        if action == 'review':
            attempt = self._ready(d, 'review')
            if attempt['agent_id'] in d['implementer_ids']: raise core.Error('Reviewer must be independent of the implementers')
            self._current(d)
            if a['verdict'] not in ('pass', 'changes'): raise core.Error('Invalid review verdict')
            if a['verdict'] == 'pass': self._clean(d)
            d['review'] = dict(agent_id=attempt['agent_id'], role=attempt['role'], configured_model=attempt['model'], configured_effort=attempt['effort'], files=d['delivery']['files'], verdict=a['verdict'], summary=text(a['summary'], 'summary'))
            d.update(phase='ready' if a['verdict'] == 'pass' else 'changes_needed', attempt=None); return
        if action == 'finish':
            if phase != 'ready' or not d['review'] or d['review']['verdict'] != 'pass':
                raise core.Error('Implementation and independent review are required')
            self._current(d)
            self._clean(d)
            d['phase'] = 'completed'; return
        raise core.Error('Invalid workflow action')

    def _clean(self, d):
        if any(r['status'] == 'open' for r in d.get('resources', {}).values()):
            raise core.Error('Task resources remain open; record cleanup evidence or the user-approved retention before finishing')

    def _resource(self, d, action, a):
        resources = d.setdefault('resources', {})
        if action == 'resource-add':
            if d['phase'] == 'completed': raise core.Error('Workflow is closed')
            if len(resources) >= 64: raise core.Error('Workflow resource limit reached')
            kind = a['kind']
            if kind not in ('process', 'tab', 'app', 'temporary', 'other'): raise core.Error('Invalid resource kind')
            locator = text(a['locator'], 'locator')
            owner = _identifier(a['owner'], 'owner', 256)
            owners = {d['session'], *d['implementer_ids']}
            if d['attempt'] and d['attempt']['agent_id']: owners.add(d['attempt']['agent_id'])
            if d['review']: owners.add(d['review']['agent_id'])
            if owner not in owners: raise core.Error('Resource owner must be an observed task agent or the coordinator session')
            if any(r['kind'] == kind and r['locator'] == locator and r['status'] == 'open' for r in resources.values()):
                raise core.Error('This resource is already recorded as open')
            resources[uuid.uuid4().hex] = dict(kind=kind, locator=locator, owner=owner,
                purpose=text(a['purpose'], 'purpose'), creation_evidence=text(a['creation_evidence'], 'creation evidence'), created_by_task=True, status='open', evidence_source='reported')
            return
        resource = resources.get(a['resource_id'])
        if not resource: raise core.Error('Resource not found')
        resource.update(status='released' if action == 'resource-release' else 'retained',
                        evidence=text(a['evidence'], 'evidence'))

    def _ready(self, d, stage):
        attempt = d['attempt']
        if not attempt or attempt['stage'] != stage or not all((attempt['agent_id'], attempt['started'], attempt['stopped'])):
            raise core.Error('Waiting for correlated dispatch, start and stop hook observations')
        return attempt

    def _current(self, d):
        if not d['delivery'] or fingerprint(self.project, list(d['delivery']['files'])) != d['delivery']['files']:
            raise core.Error('Artifacts changed; retry implementation and review the new delivery')

    def observe(self, task, session, event, *, token=None, tool_use_id=None, agent_id=None, role=None):
        """Called only by the hook adapter. Unknown/out-of-order receipts cannot advance a stage."""
        with self.context._transaction(write=True) as conn:
            d = self._get(conn, task)
            if d['session'] != session or d['phase'] in TERMINAL: return d
            attempt = d['attempt']
            if not attempt: return d
            before = json.dumps(d, sort_keys=True)
            if event == 'PreToolUse' and token == attempt['token'] and role == hosts.export().agent_name(attempt['role']) and tool_use_id:
                # One exact dispatch per token. Repeated retries need a fresh token.
                if not attempt['tool_use_id']: attempt['tool_use_id'] = tool_use_id
            elif event == 'PostToolUse' and tool_use_id and tool_use_id == attempt['tool_use_id'] and agent_id:
                if not attempt['agent_id']:
                    if attempt['stage'] == 'review' and agent_id in d['implementer_ids']: return d
                    attempt['agent_id'] = agent_id
                    if attempt['stage'] == 'implementation' and agent_id not in d['implementer_ids']:
                        d['implementer_ids'].append(agent_id)
            elif event in ('SubagentStart', 'SubagentStop') and agent_id and role == hosts.export().agent_name(attempt['role']):
                receipt = [event, agent_id, role]
                if receipt not in d['lifecycle']:
                    d['lifecycle'] = (d['lifecycle'] + [receipt])[-64:]
            if attempt['agent_id']:
                for observed, observed_id, observed_role in d['lifecycle']:
                    if observed_id == attempt['agent_id'] and observed_role == hosts.export().agent_name(attempt['role']):
                        attempt['started' if observed == 'SubagentStart' else 'stopped'] = True
            # Lifecycle may arrive before spawn return. Adapter can replay bounded receipts.
            if attempt['agent_id'] and attempt['started']:
                target = 'reviewing' if attempt['stage'] == 'review' else 'implementing'
                if d['phase'] == 'blocked': d['resume_phase'] = target
                else: d['phase'] = target
            if before == json.dumps(d, sort_keys=True): return d
            if event not in d['observed_events']: d['observed_events'].append(event)
            return self._save(conn, d)

    def correction(self, task):
        """One stop correction for an unchanged state; never prevent pause/cancel."""
        with self.context._transaction(write=True) as conn:
            d = self._get(conn, task)
            if d['phase'] in TERMINAL | {'blocked'}: return None
            key = json.dumps([d['phase'], d['attempt'], d['delivery'], d['review']], sort_keys=True)
            digest = hashlib.sha256(key.encode()).hexdigest()
            if d['correction'] == digest: return None
            d['correction'] = digest
            self._save(conn, d)
            return instruction(dict(d, task_id=task))


def instruction(d):
    next_step = {'awaiting_assignment': 'dispatch implementation', 'changes_needed': 'dispatch implementation fixes',
                 'dispatch_pending': 'use the prepared spawn marker; wait for host observations',
                 'implementing': 'wait for implementation, then record delivery', 'needs_review': 'dispatch independent review',
                 'reviewing': 'wait for review, then record verdict', 'ready': 'finish after checking artifacts',
                 'blocked': 'wait for the blocker to be resolved', 'completed': 'workflow complete; use workflow next for a new goal', 'cancelled': 'workflow cancelled; use workflow next for a new goal'}
    open_resources = sum(r['status'] == 'open' for r in d.get('resources', {}).values())
    cleanup = (' 待处理任务资源：' + str(open_resources) + '。' if i18n.LANG == 'zh' else ' Open task resources: ' + str(open_resources) + '.') if open_resources and d['phase'] not in {'blocked', 'cancelled'} else ''
    if i18n.LANG == 'zh':
        steps = {'awaiting_assignment': '派发实施', 'changes_needed': '派发修订', 'dispatch_pending': '使用派发标记启动 agent，等待宿主回执',
                 'implementing': '等待实施完成并登记交付', 'needs_review': '派发独立审查', 'reviewing': '等待审查并登记结论',
                 'ready': '核对产物后完成', 'blocked': '等待阻碍解除', 'completed': '本轮完成；新任务用 workflow next', 'cancelled': '本轮取消；新任务用 workflow next'}
        return 'Crew 工作流 ' + d['task_id'] + '：' + steps[d['phase']] + cleanup + '。编辑校验：' + d.get('edit_policy', 'stage') + '。协调者负责派发、整合与验收；实施交给配置的执行角色。变更前先用 workflow read 读取当前状态。'
    return 'Crew workflow ' + d['task_id'] + ': ' + d['phase'] + '. Next: ' + next_step[d['phase']] + cleanup + '. Edit checks: ' + d.get('edit_policy', 'stage') + '. Coordinator: delegate execution to configured roles; integrate and accept their results. Use workflow read before changing state.'
