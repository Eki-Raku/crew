import argparse
import builtins
import json
import os
from pathlib import Path
import sys
import signal
import threading
import time
from . import __version__
from . import core as c
from . import hosts
from . import i18n
from . import wizard
from . import context_cli

def print(value=''):
    builtins.print(i18n.t(value))

def output(value): print(json.dumps(i18n.localize(value), ensure_ascii=False, indent=2))

class Parser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        if 'description' in kwargs: kwargs['description'] = i18n.t(kwargs['description'])
        super().__init__(*args, **kwargs)
        if i18n.LANG == 'zh':
            self._positionals.title = '位置参数'; self._optionals.title = '选项'
            self._actions[0].help = '显示帮助并退出'
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, i18n.t('Error: ') + i18n.t(message) + '\n')
    def add_argument(self, *args, **kwargs):
        if 'help' in kwargs: kwargs['help'] = i18n.t(kwargs['help'])
        return super().add_argument(*args, **kwargs)

def parser():
    p = Parser(description='Crew: local roles, setup and hook diagnostics; no model calls')
    p.add_argument('--version', action='version', version='Crew ' + __version__)
    p.add_argument('--host', choices=hosts.HOSTS, help='Target host; defaults to CREW_HOST, then the detected environment, then codex')
    p.add_argument('--lang', choices=['zh', 'en'], help='UI language (also CREW_LANG)')
    sub = p.add_subparsers(dest='cmd', required=True)
    def command(name):
        s = sub.add_parser(name); s.add_argument('--lang', choices=['zh', 'en'], default=argparse.SUPPRESS, help='UI language'); s.add_argument('--host', choices=hosts.HOSTS, default=argparse.SUPPRESS, help='Target host; defaults to CREW_HOST, then the detected environment, then codex'); s.add_argument('--project', default=os.getcwd()); s.add_argument('--json', action='store_true', help='Machine JSON (default for non-TUI commands)'); return s
    for name in ['preflight', 'doctor', 'config', 'tui']: command(name)
    for name in ['setup', 'uninstall']:
        s = command(name); s.add_argument('--apply', action='store_true')
    s = command('enable'); s.add_argument('--scope', choices=['global', 'project'], default='project'); s.add_argument('--disabled', action='store_true')
    s = command('role'); s.add_argument('action', choices=['list', 'show', 'create', 'update', 'delete', 'disable', 'enable']); s.add_argument('name', nargs='?')
    s.add_argument('--scope', choices=['global', 'project'], default='project')
    s.add_argument('--model'); s.add_argument('--effort', choices=hosts.ALL_EFFORTS); s.add_argument('--description'); prompts = s.add_mutually_exclusive_group(); prompts.add_argument('--instructions'); prompts.add_argument('--instructions-file', help=i18n.t('UTF-8 prompt file; empty file clears the prompt'))
    s.add_argument('--apply', action='store_true', help='Confirm delete (reveals inherited global role); disable is usually safer')
    s = command('init'); s.add_argument('--scope', choices=['global', 'project'], default='project'); s.add_argument('--answer', help='JSON value answering the current step'); s.add_argument('--step'); s.add_argument('--revision', type=int); s.add_argument('--apply', action='store_true'); s.add_argument('--confirm-token'); s.add_argument('--reset', action='store_true'); s.add_argument('--models-file', help='JSON array of model IDs supplied by the host or user'); s.add_argument('--interactive', action='store_true')
    s = command('migrate'); s.add_argument('--scope', choices=['global', 'project', 'all'], default='all'); s.add_argument('--apply', action='store_true')
    s = command('presets'); s.add_argument('action', choices=['list', 'install']); s.add_argument('names', nargs='*'); s.add_argument('--scope', choices=['global', 'project'], default='project'); s.add_argument('--model'); s.add_argument('--effort', choices=hosts.ALL_EFFORTS); s.add_argument('--prompt-lang', choices=['zh', 'en'], help='Built-in prompt language (defaults to UI language)')
    s = command('export'); s.add_argument('--scope', choices=['global', 'project'], default='project'); s.add_argument('--apply', action='store_true'); s.add_argument('--clean', action='store_true')
    s = command('hook'); s.add_argument('action', choices=['receive', 'ping', 'status'])
    context_cli.register(command)
    return p

def run(args):
    project = str(c.project_root(args.project))
    if args.cmd == 'context': return context_cli.run(args, project)
    if args.cmd == 'preflight': return c.preflight(project)
    if args.cmd == 'doctor': return c.doctor(project)
    if args.cmd == 'config': return dict(c.effective(project), host_source=hosts.SOURCE, home=str(c.home()))
    if args.cmd == 'setup': return c.setup(args.apply)
    if args.cmd == 'uninstall': return c.uninstall(args.apply)
    if args.cmd == 'enable': return c.set_enabled(args.scope, project, not args.disabled)
    if args.cmd == 'export': return c.export_agents(project, args.scope, args.apply, args.clean)
    if args.cmd == 'role':
        roles = c.effective(project)['roles']
        if args.action == 'list': return roles
        if not args.name: raise c.Error('Role name required')
        if args.action == 'show':
            if args.name not in roles: raise c.Error('Role not found')
            return roles[args.name]
        if args.action == 'delete' and not args.apply: raise c.Error('Use --apply to delete; inherited global role may reappear. Use disable to mask it.')
        instructions = Path(args.instructions_file).expanduser().read_text(encoding='utf-8') if args.instructions_file is not None else args.instructions
        description, copied = args.description, None
        if args.action == 'create':
            # A role first defined for one host starts the other host's copy from the same text.
            # It is a one-time copy: later edits on either host stay on that host.
            entry = c.read(c.path(args.scope, project))['roles'].get(args.name)
            for other in hosts.HOSTS:
                base = hosts.view(entry, other) if other != hosts.CURRENT else None
                if not base or 'model' not in base: continue
                fields = [k for k, v in (('description', description), ('instructions', instructions)) if v is None]
                if description is None: description = base['description']
                if instructions is None: instructions = base['instructions']
                if fields: copied = {'host': other, 'fields': fields}
                break
            if instructions is None: instructions = ''
        value = {'model': args.model, 'effort': args.effort, 'description': description, 'instructions': instructions}
        result = c.role_write(args.scope, project, args.name, value, args.action)
        if copied: result['copied_from'] = copied
        return result
    if args.cmd == 'presets':
        if args.action == 'list': return {'presets': c.presets(args.prompt_lang), 'model_selection': 'Choose model and effort when installing; editable afterwards.'}
        if not args.model or not args.effort: raise c.Error('Preset install requires --model and --effort from your host model picker')
        return c.install_presets(args.scope, project, args.names or list(c.presets()), args.model, args.effort, args.prompt_lang)
    if args.cmd == 'migrate':
        from . import migration
        return migration.migrate(project, args.scope, args.apply)
    if args.cmd == 'init':
        models = json.loads(Path(args.models_file).read_text()) if args.models_file else None
        if args.interactive:
            if args.answer is not None: raise c.Error('Use either --interactive or --answer')
            return guided_init(project, args.scope, models, args.reset, args.apply)
        answer = json.loads(args.answer) if args.answer is not None else None
        if args.answer is not None and answer is None: raise c.Error('Answer cannot be null')
        return wizard.advance(args.scope, project, answer, args.step, args.revision, args.apply, args.confirm_token, args.reset, models)
    if args.cmd == 'hook':
        if args.action == 'status': return c.doctor(project)['hook_receipts']
        if args.action == 'ping':
            row = c.record({'hook_event_name': 'SelfTest', 'session_id': 'selftest'}, project, simulated=True)
            return {'status': 'selftest_passed', 'receipt': row, 'host_verified': False}
        return hook_receive(project)
    if args.cmd == 'tui': return tui(project)

def hook_notice(event, message):
    """Tell the user without failing the turn; only session start is worth a visible line."""
    if isinstance(event, dict) and event.get('hook_event_name') not in (None, 'SessionStart'): return {}
    return {'systemMessage': message}

def hook_banner(state):
    """One line shown to the user each time the plugin loads: which host Crew found and what it uses."""
    host, evidence = hosts.CURRENT, hosts.DETECTED[1]
    text = i18n.f('Crew {version}: {label} environment detected ({evidence}).', version=__version__, label=hosts.label(), evidence=evidence) if hosts.DETECTED[0] == host else \
        i18n.f('Crew {version}: running as {label} ({source}); no environment marker was found.', version=__version__, label=hosts.label(), source=hosts.SOURCE)
    text += ' ' + i18n.f('Using {label} roles and tasks only: {count} role(s), status {status}.', label=hosts.label(), count=len(state['roles']), status=state['status'])
    if state['roles_for_other_hosts_only'] and not state['roles']:
        text += ' ' + i18n.f('{count} role(s) are configured for the other host only.', count=len(state['roles_for_other_hosts_only']))
    return text

class HookDeadline(BaseException):
    """Bypass inner diagnostic handlers, so a spent budget cannot restart work."""


def hook_receive(project):
    # Supported hosts run hooks in a Unix main process. Bound *all* work, not
    # each SQLite/Git/receipt wait separately, below the shortest host timeout.
    deadline = time.monotonic() + 2.0
    timed = hasattr(signal, 'setitimer') and threading.current_thread() is threading.main_thread()
    if timed:
        old_handler = signal.getsignal(signal.SIGALRM)
        old_timer = signal.getitimer(signal.ITIMER_REAL)
        started = time.monotonic()
        def expired(signum, frame): raise HookDeadline()
        signal.signal(signal.SIGALRM, expired)
        signal.setitimer(signal.ITIMER_REAL, min(2.5, old_timer[0]) if old_timer[0] else 2.5)
    try:
        return _hook_receive(project, deadline)
    except HookDeadline:
        return {'systemMessage': 'Crew hook exceeded its time budget. Continue with current work; shared context is temporarily unavailable. No lifecycle deletion was inferred.'}
    finally:
        if timed:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old_handler)
            if old_timer[0]:
                signal.setitimer(signal.ITIMER_REAL, max(.001, old_timer[0] - (time.monotonic() - started)), old_timer[1])


def _hook_receive(project, deadline):
    """Handle one host hook event. Never exits nonzero: exit code 2 would block the user's prompt."""
    event = None
    try:
        raw = sys.stdin.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024: raise c.Error('Hook input too large')
        try: event = json.loads(raw)
        except ValueError as e: raise c.Error(f'Invalid hook JSON: {e}')
        if not isinstance(event, dict): event = None; raise c.Error('Hook input must be JSON object')
        name = event.get('hook_event_name')
        actual = hosts.mismatch()
        if actual and hosts.SOURCE == 'flag':
            # The other host's hooks file was loaded here. Touch nothing and say so.
            return hook_notice(event, i18n.f('Crew: the {declared} hooks file was loaded inside {actual} ({evidence}). Nothing was read or written. Use hooks/hooks.json for Claude Code and hooks/codex.json for Codex.',
                                             declared=hosts.label(), actual=hosts.label(actual), evidence=hosts.DETECTED[1]))
        project = event.get('cwd', project)
        if not isinstance(project, str): raise c.Error('Invalid cwd')
        try:
            c.record(event, project, lock_timeout=.15)
            from . import context_hooks
            result = context_hooks.handle(event, project, deadline=deadline)
            if name == 'SessionStart':
                state = c.preflight(project)
                summary = {k: state[k] for k in ('host', 'status', 'enabled', 'roles', 'stale_exports')}
                extra = 'Crew configuration: ' + json.dumps(summary, ensure_ascii=False, separators=(',', ':'))
                if result:
                    result['hookSpecificOutput']['additionalContext'] += '\n' + extra
                else:
                    result = {'hookSpecificOutput': {'hookEventName': 'SessionStart', 'additionalContext': extra}}
                if event.get('source') != 'compact': result['systemMessage'] = hook_banner(state)
            return result
        except (c.Error, OSError, ValueError) as exc:
            if name in ('SessionStart', 'UserPromptSubmit', 'SubagentStart'):
                return {'hookSpecificOutput': {'hookEventName': name, 'additionalContext': 'Crew state could not be read: ' + str(exc)[:400] + '. Continue cautiously; no record was deleted.'}}
            return {}
    except Exception as exc:  # Any failure here must stay a notice, never a blocked prompt.
        return hook_notice(event, i18n.t('Crew hook skipped: ') + i18n.t(str(exc))[:400])

def ask(label, default=''):
    value = input(i18n.t(label) + (f' [{default}]' if default else '') + ': ').strip()
    return value or default

def model_prompt():
    return 'Model ID from your Codex picker' if hosts.CURRENT == 'codex' else 'Claude Code model ID, alias or inherit'

def edit_prompt(current='', existing=False):
    print('Current prompt: ' + (current if current.strip() else '[empty agent / 空 agent]'))
    print('1 Write multiline prompt  2 Load UTF-8 file  3 Clear (empty agent)' + ('  4 Keep current' if existing else ''))
    choice = ask('Prompt action', '4' if existing else '1')
    if choice == '4' and existing: return current
    if choice == '3': return ''
    if choice == '2': return Path(ask('Prompt file')).expanduser().read_text(encoding='utf-8')
    if choice != '1': raise c.Error('Invalid prompt action')
    print('Enter prompt; finish with a single . line. Immediate . creates an empty agent.')
    lines = []
    while True:
        line = input()
        if line == '.': return '\n'.join(lines)
        lines.append(line)


def guided_init(project, scope='project', models=None, reset=False, apply=False):
    if not sys.stdin.isatty() or not sys.stdout.isatty(): raise c.Error('TUI requires a terminal; use init --json for host-guided questions')
    state = wizard.advance(scope, project, models=models, reset=reset, apply=apply)
    while state['status'] != 'complete':
        step = state['step']
        print(step['question']); choices = step.get('choices', [])
        if step['id'] == 'confirm':
            output(step['preview'])
            if ask('Save? y/N').lower() != 'y': return dict(state, message='Draft saved. Resume with init --interactive.')
            value = True
        else:
            for index, item in enumerate(choices, 1):
                label = item['value'] + ': ' + item['label'] if isinstance(item, dict) else str(item)
                print(str(index) + '. ' + label)
            if step['type'] == 'select_or_text': print('Select a number, or enter a model ID. Prefix a numeric model ID with =.')
            raw = ask('Select (comma-separated for multiple roles; q to pause)')
            if raw.lower() == 'q': return dict(state, message='Draft saved. Resume with init --interactive.')
            try:
                if step['type'] == 'multi_select':
                    numbers = [int(x.strip()) for x in raw.split(',')]
                    if any(n < 1 or n > len(choices) for n in numbers): raise ValueError()
                    value = [choices[n-1]['value'] for n in numbers]
                elif step['type'] == 'single_select':
                    number = int(raw)
                    if number < 1 or number > len(choices): raise ValueError()
                    value = choices[number-1]
                else:
                    value = raw[1:] if raw.startswith('=') else (choices[int(raw)-1] if raw.isdigit() and 1 <= int(raw) <= len(choices) else raw)
                state = wizard.advance(scope, project, value, step['id'], state['revision'])
            except (ValueError, c.Error) as e:
                print('Error: ' + (str(e) or 'Invalid action'))
            continue
        state = wizard.advance(scope, project, value, step['id'], state['revision'], True, step['preview']['confirmation_token'])
    return state


def tui(project):
    if not sys.stdin.isatty() or not sys.stdout.isatty(): raise c.Error('TUI requires a terminal; use init --json for host-guided questions')
    scope = 'project'
    while True:
        print('\n╭─ Crew · interactive terminal ─╮')
        print(f'Project: {project}\nScope: {scope}')
        print(i18n.t('Host: ') + hosts.label())
        if not c.effective(project)['roles']: print('Start with 10 for guided setup, or 2 for a custom/empty agent.')
        print('1 Roles  2 Create/update  3 Enable/disable/delete  4 Setup entry\n5 Export agents  6 Doctor / Hook ping  7 Scope  8 Enable/disable  9 Built-in roles  10 Guided setup  11 Shared context  12 Switch host  0 Exit')
        try:
            choice = ask('Select')
            if choice == '0': return {'status': 'closed'}
            if choice == '1': output(c.effective(project))
            elif choice == '2':
                name = ask('Role id'); old = c.effective(project)['roles'].get(name, {})
                model = ask(model_prompt(), old.get('model', ''))
                print('Effort options: ' + ', '.join(hosts.efforts()))
                effort = ask('Supported effort', old.get('effort', 'low'))
                value = {'model': model, 'effort': effort, 'description': ask('Description', old.get('description', '')),
                         'instructions': edit_prompt(old.get('instructions', ''), existing=name in c.effective(project)['roles'])}
                action = 'update' if hosts.view(c.read(c.path(scope, project))['roles'].get(name)) is not None else 'create'
                c.validate_name(name); c.validate_view(value); output(dict(value, prompt_state='configured' if value['instructions'].strip() else 'empty_agent'))
                if ask('Save? y/N').lower() == 'y': output(c.role_write(scope, project, name, value, action))
            elif choice == '3':
                name = ask('Role id'); action = ask('Action: enable, disable or delete', 'disable')
                if action not in ('enable', 'disable', 'delete'): raise c.Error('Invalid action')
                if ask('Confirm? y/N').lower() == 'y': output(c.role_write(scope, project, name, None, action))
            elif choice == '4':
                output(c.setup(False))
                if ask('Apply global entry? y/N').lower() == 'y': output(c.setup(True))
            elif choice == '5':
                output(c.export_agents(project, scope))
                if ask('Apply export? y/N').lower() == 'y': output(c.export_agents(project, scope, True))
            elif choice == '6':
                output(c.doctor(project))
                if ask('Run simulated Hook ping? y/N').lower() == 'y': output(c.record({'hook_event_name': 'SelfTest'}, project, True))
            elif choice == '11': output(context_cli.menu(project, ask, output))
            elif choice == '10': output(guided_init(project, scope))
            elif choice == '9':
                output(c.presets())
                names = ask('Role ids separated by commas; empty = all').strip()
                selected = [n.strip() for n in names.split(',')] if names else list(c.presets())
                model = ask(model_prompt()); effort = ask('Supported effort', 'low')
                if ask('Import missing roles? Existing roles are preserved. y/N').lower() == 'y':
                    output(c.install_presets(scope, project, selected, model, effort))
            elif choice == '7': scope = 'global' if scope == 'project' else 'project'
            elif choice == '12': hosts.select(next(h for h in hosts.HOSTS if h != hosts.CURRENT))
            elif choice == '8':
                enabled = ask('Enable? y/N').lower() == 'y'; output(c.set_enabled(scope, project, enabled))
        except (c.Error, OSError, ValueError) as e: print('Error: ' + str(e))

def main():
    # Parse language first so help and errors use the selected language.
    language_parser = argparse.ArgumentParser(add_help=False)
    language_parser.add_argument('--lang', choices=['zh', 'en'])
    language_parser.add_argument('--host', choices=hosts.HOSTS)
    selected, _ = language_parser.parse_known_args()
    i18n.set_language(selected.lang)
    hosts.select(selected.host)  # Before building the parser: paths and choices depend on the host.
    argparse._ = i18n.t  # Localize argparse's standard usage/error labels.
    try:
        args = parser().parse_args()
        result = run(args)
        if args.cmd == 'context': print(json.dumps(i18n.localize(result), ensure_ascii=False, separators=(',', ':')))
        else: output(result)
    except (c.Error, OSError, ValueError) as e:
        error = {'status': 'error', 'message': str(e), 'error_type': type(e).__name__}
        for field in ('expected_version', 'current_version'):
            if hasattr(e, field): error[field] = getattr(e, field)
        output(error); sys.exit(2)
    except (KeyboardInterrupt, EOFError):
        output({'status': 'cancelled'}); sys.exit(130)

if __name__ == '__main__': main()
