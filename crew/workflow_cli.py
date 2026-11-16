"""CLI/TUI for explicit engineering workflows; no host process is launched."""
from . import core, hosts, i18n
from .context_cli import required
from .workflow import WorkflowStore, instruction, slot_label


def register(command):
    p = command('workflow')
    p.add_argument('action', choices=['start', 'next', 'read', 'report', 'dispatch', 'retry', 'deliver', 'review', 'finish', 'pause', 'resume', 'cancel', 'resource-add', 'resource-release', 'resource-retain'])
    p.add_argument('--task'); p.add_argument('--session'); p.add_argument('--expected-version', type=int)
    p.add_argument('--edit-policy', choices=['identity', 'stage'], help='Identity checks for edits, or explicit stage-only compatibility')
    p.add_argument('--goal'); p.add_argument('--acceptance'); p.add_argument('--implementer'); p.add_argument('--reviewer')
    p.add_argument('--files', nargs='+'); p.add_argument('--summary'); p.add_argument('--tests')
    p.add_argument('--resource-id'); p.add_argument('--kind', choices=['process', 'tab', 'app', 'temporary', 'other'])
    p.add_argument('--locator'); p.add_argument('--owner'); p.add_argument('--purpose'); p.add_argument('--creation-evidence'); p.add_argument('--evidence')
    p.add_argument('--verdict', choices=['pass', 'changes']); p.add_argument('--reason')
    p.add_argument('--count', type=int, help='Number of parallel role instances to dispatch (up to role max_instances)')


def _slots(data):
    if data.get('attempts'): return data['attempts']
    return [data['attempt']] if data.get('attempt') else []


def present(data):
    result = dict(data, next=instruction(data), edit_protection=data.get('edit_policy', 'stage'), verification='local_hook_observations',
                  host_enforcement='not_verified')
    slots = _slots(data)
    if slots:
        total = len(slots)
        inputs = [{
            # Pass agent_type, task_name and message to spawn_agent exactly as given. The PreToolUse gate
            # rejects any other value; the names are fixed by the dispatch, not chosen by the caller.
            'agent_type': hosts.export().agent_name(slot['role']),
            'task_name': slot.get('task_name'),
            'name': slot_label(slot, idx, total),
            'native_nickname_candidates': slot.get('native_nicknames') or [],
            'model': slot['model'],
            'reasoning_effort': slot['effort'],
            'message': dispatch_message(data, slot=slot, index=idx if total > 1 else None, total=total),
        } for idx, slot in enumerate(slots, 1)]
        result['dispatch_input'] = inputs[0]
        if total > 1:
            result['dispatch_inputs'] = inputs
    return result


def report(data):
    """A compact account of stored evidence, not model usage statistics."""
    result = {k: data[k] for k in ('task_id', 'version', 'goal', 'acceptance', 'phase')}
    result.update(next=instruction(data), edit_protection=data.get('edit_policy', 'stage'),
                  host_enforcement='not_verified', evidence={'dispatch': 'local_hook_observations', 'tests_and_cleanup': 'reported'})
    if data.get('reason'): result['waiting_or_cancel_reason'] = data['reason']
    if data.get('resume_evidence'): result['resume_evidence'] = data['resume_evidence']
    slots = _slots(data)
    if slots:
        total = len(slots)
        assignments = []
        for idx, slot in enumerate(slots, 1):
            item = {k: slot[k] for k in ('stage', 'role', 'agent_id', 'started', 'stopped')}
            item.update(subagent_name=slot_label(slot, idx, total), task_name=slot.get('task_name'),
                        native_nickname_candidates=slot.get('native_nicknames') or [],
                        native_nickname_observed=slot.get('native_nickname'),
                        configured_model=slot['model'], configured_effort=slot['effort'])
            assignments.append(item)
        result['assignment'] = assignments[0]
        if total > 1:
            result['assignments'] = assignments
    for name in ('delivery', 'review'):
        if data.get(name):
            result[name] = {k: v for k,v in data[name].items() if k != 'files'}
            names = data[name].get('subagent_names')
            result[name]['subagent_name'] = names[0] if names else hosts.subagent_name(data[name]['role'], data[name]['configured_model'])
            result[name]['artifact_paths'] = list(data[name]['files'])
    result['resources'] = [{'id':key, **value} for key,value in data.get('resources', {}).items()]
    return result


def dispatch_message(data, slot=None, index=None, total=1):
    import json
    attempt = slot or data['attempt']
    name = slot_label(attempt, index, total)
    header = '[crew-dispatch:' + attempt['token'] + ']\n'
    header += (('子智能体命名：' + name + '\n登记任务创建的进程、页签或临时产物；退出前用原工具清理，汇报处置证据或用户要求保留的说明。\n')
               if i18n.LANG == 'zh' else
               ('Subagent name: ' + name + '\nRecord task-created processes, tabs or temporary artifacts; clean them up with their original tools and report evidence or the user instruction to retain them.\n'))
    if i18n.LANG == 'zh':
        if attempt['stage'] == 'review':
            return header + '独立审查这份交付。检查列出的文件、验收要求和测试证据，返回 pass 或 changes、具体发现与验证空白。\n目标：' + data['goal'] + '\n验收：' + data['acceptance'] + '\n交付：' + json.dumps(data['delivery'], ensure_ascii=False) + '\n待核验资源：' + json.dumps(data.get('resources', {}), ensure_ascii=False)
        return header + '实现目标并运行相关测试。交付改动文件路径、测试证据与遗留问题。\n目标：' + data['goal'] + '\n验收：' + data['acceptance'] + ('\n上轮审查：' + json.dumps(data['previous_review'], ensure_ascii=False) if data.get('previous_review') else '')
    if attempt['stage'] == 'review':
        return header + 'Independently review this delivery against the goal. Inspect the listed files and test evidence. Return pass or changes, concrete findings and verification gaps.\nGoal: ' + data['goal'] + '\nAcceptance: ' + data['acceptance'] + '\nDelivery: ' + json.dumps(data['delivery'], ensure_ascii=False) + '\nResources to verify: ' + json.dumps(data.get('resources', {}), ensure_ascii=False)
    return header + 'Implement the goal and run applicable tests. Report changed artifact paths, test evidence and remaining issues.\nGoal: ' + data['goal'] + '\nAcceptance: ' + data['acceptance'] + ('\nPrevious review: ' + json.dumps(data.get('previous_review'), ensure_ascii=False) if data.get('previous_review') else '')


def run(args, project):
    if hosts.CURRENT != 'codex': raise core.Error('Engineering workflow currently requires Codex hooks')
    if args.edit_policy is not None and args.action not in ('start', 'next'):
        raise core.Error('Edit policy can be selected only when starting a workflow cycle')
    if args.count is not None and args.action != 'dispatch':
        raise core.Error('--count can only be used with dispatch')
    store = WorkflowStore(project)
    if args.action == 'start':
        required(args, 'session', 'goal', 'implementer', 'reviewer', 'acceptance')
        if not core.effective(project)['enabled']: raise core.Error('Crew is disabled')
        if args.task is None:
            args.task = store.context.open_session(hosts.CURRENT, args.session)['task_id']
        return present(store.start(args.task, args.session, args.goal, args.implementer, args.reviewer, args.acceptance, args.edit_policy or "identity"))
    required(args, 'task')
    if args.action == 'read': return present(store.read(args.task))
    if args.action == 'report': return report(store.read(args.task))
    required(args, 'expected_version')
    fields = {'resource-add': ['kind', 'locator', 'owner', 'purpose', 'creation_evidence'], 'resource-release': ['resource_id', 'evidence'], 'resource-retain': ['resource_id', 'evidence'], 'next': ['goal', 'acceptance'], 'retry': ['reason'], 'pause': ['reason'], 'resume': ['reason'], 'cancel': ['reason'],
              'deliver': ['files', 'summary', 'tests'], 'review': ['verdict', 'summary']}.get(args.action, [])
    required(args, *fields)
    values = {f: getattr(args, f) for f in fields}
    if args.action == 'next' and args.edit_policy is not None: values['edit_policy'] = args.edit_policy
    if args.action == 'dispatch' and args.count is not None: values['count'] = args.count
    return present(store.change(args.task, args.expected_version, args.action, **values))


def menu(project, ask, output):
    if hosts.CURRENT != 'codex': raise core.Error('Engineering workflow currently requires Codex hooks')
    store = WorkflowStore(project)
    task = ask('Task ID (empty starts a workflow)')
    if not task:
        session = ask('Session ID'); goal = ask('Goal'); acceptance = ask('Acceptance criteria')
        implementer = ask('Implementation role', 'implementer'); reviewer = ask('Review role', 'reviewer')
        policy = ask('Edit policy: identity/stage', 'identity')
        if ask('Save? y/N').lower() != 'y': return {'status': 'cancelled'}
        task = store.context.open_session(hosts.CURRENT, session)['task_id']
        store.start(task, session, goal, implementer, reviewer, acceptance, policy)
    while True:
        data = store.read(task); output(report(data))
        output({'resource_actions': 'resource-add / resource-release / resource-retain'})
        action = ask('Workflow action: next/dispatch/retry/deliver/review/finish/pause/resume/cancel; q exits', 'q')
        if action == 'q': return {'status': 'closed'}
        args = {}
        if action == 'next':
            args['goal'] = ask('Goal'); args['acceptance'] = ask('Acceptance criteria')
            args['edit_policy'] = ask('Edit policy: identity/stage', data.get('edit_policy', 'stage'))
        if action == 'dispatch':
            args['count'] = int(ask('Parallel instance count', '1'))
        if action == 'resource-add':
            args.update(kind=ask('Resource kind: process/tab/app/temporary/other'), locator=ask('Resource locator'), owner=ask('Resource owner'), purpose=ask('Resource purpose'), creation_evidence=ask('Resource creation evidence'))
        if action in ('resource-release', 'resource-retain'):
            args.update(resource_id=ask('Resource ID'), evidence=ask('Cleanup evidence or user retention instruction'))
        if action in ('retry', 'pause', 'resume', 'cancel'): args['reason'] = ask('Reason')
        if action in ('deliver', 'review'): args['summary'] = ask('Summary')
        if action == 'deliver':
            args['files'] = ask('Artifact paths separated by commas').split(',')
            args['tests'] = ask('Test evidence')
        if action == 'review': args['verdict'] = ask('Review verdict: pass/changes')
        if ask('Save? y/N').lower() != 'y': continue
        try: output(present(store.change(task, data['version'], action, **args)))
        except (core.Error, OSError, ValueError) as exc: output({'error': i18n.t(str(exc))})
