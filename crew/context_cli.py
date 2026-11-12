"""Command and terminal adapters for the shared state store."""
import json
from pathlib import Path
from . import core as c
from . import hosts
from . import i18n

def say(value): print(i18n.t(value))


def register(command):
    p = command('context')
    p.add_argument('action', choices=['open', 'read', 'patch', 'checkpoint', 'list', 'archive', 'delete', 'restore', 'session-state', 'decisions', 'decision-set', 'decision-delete', 'decision-restore', 'stats'])
    p.add_argument('--provider', choices=['codex', 'claude'], help='Session provider; must match --host (defaults to the selected host)')
    p.add_argument('--session'); p.add_argument('--task'); p.add_argument('--title')
    p.add_argument('--since', type=int); p.add_argument('--expected-version', type=int)
    p.add_argument('--file', help='UTF-8 JSON state patch or checkpoint')
    p.add_argument('--request-id', help='Stable idempotency key for a retried write')
    p.add_argument('--include-archived', action='store_true'); p.add_argument('--include-deleted', action='store_true')
    p.add_argument('--apply', action='store_true')
    p.add_argument('--state', choices=['active', 'archived', 'deleted', 'unknown'])
    p.add_argument('--expected-task'); p.add_argument('--expected-generation', type=int)
    p.add_argument('--scope', choices=['global', 'project'], default='project')
    p.add_argument('--key'); p.add_argument('--value')
    return p


def required(args, *names):
    for name in names:
        if getattr(args, name) is None: raise c.Error('Missing argument: --' + name.replace('_', '-'))


def selected_provider(provider):
    provider = provider or hosts.CURRENT
    if provider != hosts.CURRENT:
        raise c.Error(f'Provider {provider} does not match selected host {hosts.CURRENT}; rerun with --host {provider} to use its context store')
    return provider


def run(args, project):
    from .context_store import ContextStore
    args.provider = selected_provider(args.provider)
    store = ContextStore(project)
    action = args.action
    if action == 'open':
        required(args, 'session')
        return store.open_session(args.provider, args.session, task_id=args.task, title=args.title)
    if action == 'read':
        required(args, 'task'); return store.read(args.task, since=args.since)
    if action in ('patch', 'checkpoint'):
        required(args, 'task', 'expected_version', 'file')
        data = json.loads(Path(args.file).expanduser().read_text(encoding='utf-8'))
        return getattr(store, action)(args.task, args.expected_version, data, request_id=args.request_id)
    if action == 'list': return store.list_tasks(include_archived=args.include_archived)
    if action in ('archive', 'delete', 'restore'):
        required(args, 'task')
        if not args.apply: return {'operation': action, 'task_id': args.task, 'applied': False, 'next': 'Review the task ID, then repeat with --apply.'}
        return getattr(store, action)(args.task)
    if action == 'session-state':
        required(args, 'session', 'state', 'expected_task')
        if not args.apply: return {'operation': action, 'provider': args.provider, 'session_id': args.session, 'state': args.state, 'expected_task_id': args.expected_task, 'applied': False}
        return store.session_state(args.provider, args.session, args.state, source='manual', expected_task_id=args.expected_task, expected_generation=args.expected_generation)
    if action == 'decisions': return store.decision_list(scope=args.scope, include_deleted=args.include_deleted)
    if action == 'decision-set':
        required(args, 'key', 'value', 'expected_version')
        return store.decision_put(args.key, args.value, scope=args.scope, expected_version=args.expected_version)
    if action in ('decision-delete', 'decision-restore'):
        required(args, 'key', 'expected_version')
        if not args.apply: return {'operation': action, 'key': args.key, 'scope': args.scope, 'applied': False}
        return getattr(store, 'decision_delete' if action == 'decision-delete' else 'decision_restore')(args.key, scope=args.scope, expected_version=args.expected_version)
    if action == 'stats': return store.stats()


def menu(project, ask, output):
    from .context_store import ContextStore
    store = ContextStore(project)
    while True:
        say('\nCrew shared context')
        say('1 List  2 Open/link  3 Read  4 Patch\n5 Checkpoint  6 Archive/delete/restore  7 Session state\n8 Decisions  9 Capacity  0 Back')
        choice = ask('Select')
        if choice == '0': return {'status': 'closed'}
        try:
            if choice == '1': output(store.list_tasks(include_archived=True))
            elif choice == '2':
                provider = selected_provider(ask('Provider (codex/claude)', hosts.CURRENT))
                session = ask('Session ID'); task = ask('Shared task ID (empty creates/resumes)') or None
                output(store.open_session(provider, session, task_id=task, title=ask('Title') or None))
            elif choice == '3':
                task = ask('Task ID'); since = ask('Since version (empty = snapshot)')
                output(store.read(task, since=int(since) if since else None))
            elif choice in ('4', '5'):
                task = ask('Task ID'); version = int(ask('Expected version'))
                data = json.loads(Path(ask('JSON file')).expanduser().read_text())
                output({'state': data})
                if ask('Save? y/N').lower() == 'y':
                    output(getattr(store, 'patch' if choice == '4' else 'checkpoint')(task, version, data))
            elif choice == '6':
                task = ask('Task ID'); operation = ask('archive/delete/restore', 'archive')
                if operation not in ('archive', 'delete', 'restore'): raise c.Error('Invalid action')
                if ask('Confirm? y/N').lower() == 'y': output(getattr(store, operation)(task))
            elif choice == '7':
                provider = selected_provider(ask('Provider (codex/claude)', hosts.CURRENT)); session = ask('Session ID')
                task = ask('Current task ID'); status = ask('active/archived/deleted/unknown', 'unknown')
                if ask('Confirm? y/N').lower() == 'y': output(store.session_state(provider, session, status, source='manual', expected_task_id=task))
            elif choice == '8':
                scope = ask('global/project', 'project'); output(store.decision_list(scope=scope, include_deleted=True))
                operation = ask('list/set/delete/restore', 'list')
                if operation == 'list': continue
                key = ask('Decision key'); version = int(ask('Expected version', '0'))
                if operation == 'set':
                    value = ask('Decision text')
                    if ask('Save? y/N').lower() == 'y': output(store.decision_put(key, value, scope=scope, expected_version=version))
                elif operation in ('delete', 'restore'):
                    if ask('Confirm? y/N').lower() == 'y': output(getattr(store, 'decision_delete' if operation == 'delete' else 'decision_restore')(key, scope=scope, expected_version=version))
                else: raise c.Error('Invalid action')
            elif choice == '9':
                output(store.stats())
        except (c.Error, ValueError, OSError) as e: say(str(e))
