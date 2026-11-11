"""Resumable initialization protocol shared by terminal and host-guided UI."""
import hashlib
import json
import time
from . import core as c
from . import hosts
from . import i18n


def state_path(scope, project):
    # One draft per host, so a Codex setup and a Claude Code setup never overwrite each other.
    return c.path(scope, project).with_name('onboarding.json' if hosts.CURRENT == 'codex' else 'onboarding.' + hosts.CURRENT + '.json')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def existing(scope, project):
    if scope == 'project': return c.effective(project)['roles']
    return hosts.views(c.read(c.path(scope, project)))


def preview(state, scope, project):
    old = existing(scope, project)
    additions = {name: role for name, role in state['roles'].items() if name not in old}
    value = {'host': hosts.CURRENT, 'target': str(c.path(scope, project)), 'add': additions,
             'preserved_existing': sorted(old), 'skipped_existing': sorted(set(state['roles']) & set(old)),
             'enabled_preserved': True, 'writes_host_files': False}
    # Include the complete current configuration so changes require fresh consent.
    value['confirmation_token'] = digest({'preview': value, 'existing': old,
        'config': c.read(c.path(scope, project))})
    return value


def next_step(state, scope, project):
    templates = c.presets(state['language'])
    if state.get('completed'):
        return {'id': 'done', 'type': 'complete', 'message': 'Configuration saved. Preview export next.'}
    if state['selected'] is None:
        return {'id': 'roles', 'type': 'multi_select', 'question': 'Choose the roles to add.',
                'choices': [{'value': name, 'label': role['description'], 'instructions': role['instructions']} for name, role in templates.items()],
                'existing_roles': sorted(existing(scope, project)), 'minimum': 1}
    for name in state['selected']:
        if name in existing(scope, project): continue
        role = state['roles'].get(name, {})
        if 'model' not in role:
            known = sorted({r['model'] for r in existing(scope, project).values() if 'model' in r})
            return {'id': name + '.model', 'type': 'select_or_text', 'role': name,
                    'question': 'Choose a model ID available in your host.' if hosts.CURRENT == 'codex' else 'Choose a Claude Code model: a full model ID, an alias such as sonnet or opus, or inherit.',
                    'choices': state['models'] or known, 'allow_custom': True,
                    'availability': 'user_supplied_not_live_verified'}
        if 'effort' not in role:
            return {'id': name + '.effort', 'type': 'single_select', 'role': name,
                    'question': 'Choose an effort supported by that model.', 'choices': list(hosts.efforts()),
                    'availability': 'syntax_only_check_host_support'}
    plan = preview(state, scope, project)
    return {'id': 'confirm', 'type': 'confirmation', 'question': 'Save this configuration?',
            'choices': [True, False], 'preview': plan, 'requires_apply': True}


def view(state, scope, project):
    return {'schema_version': 1, 'host': hosts.CURRENT, 'status': 'complete' if state.get('completed') else 'draft',
            'revision': state['revision'], 'draft_file': str(state_path(scope, project)),
            'step': next_step(state, scope, project),
            'presentation': 'Host may render choices or ask them as text. CLI cannot require a native selector.'}


def advance(scope, project, answer=None, step=None, revision=None, apply=False, token=None, reset=False, models=None):
    p = state_path(scope, project)
    with c.lock(p):
        if reset and not apply: raise c.Error('Reset requires --apply; saved role configuration is retained')
        if p.exists() and not reset:
            try: state = json.loads(p.read_text())
            except (ValueError, OSError) as e: raise c.Error(f'Invalid onboarding draft: {e}')
            if not isinstance(state, dict) or state.get('schema_version') != 1: raise c.Error('Unsupported onboarding draft')
            if not isinstance(state.get('revision'), int) or not isinstance(state.get('roles'), dict) or not isinstance(state.get('models'), list) or state.get('language') not in ('zh', 'en') or not isinstance(state.get('completed'), bool): raise c.Error('Invalid onboarding draft fields')
            if state.get('selected') is not None and (not isinstance(state['selected'], list) or any(not isinstance(n, str) or n not in c.presets(state['language']) for n in state['selected'])): raise c.Error('Invalid onboarding selection')
            if any(not isinstance(r, dict) for r in state['roles'].values()): raise c.Error('Invalid onboarding role data')
        else:
            state = {'schema_version': 1, 'revision': time.time_ns() if reset else 0, 'language': i18n.LANG,
                     'selected': None, 'roles': {}, 'models': models or [], 'completed': False}
        if models is not None:
            if not isinstance(models, list) or any(not isinstance(v, str) or not v.strip() for v in models):
                raise c.Error('Models must be a JSON array of nonempty model IDs')
            if state['models'] != models:
                if answer is not None: raise c.Error('Update model choices separately before answering')
                state['models'] = models; state['revision'] += 1
        if answer is not None:
            current = next_step(state, scope, project)
            if revision != state['revision'] or step != current['id']: raise c.Error('Stale answer; read init again for the current step and revision')
            if step == 'roles':
                if not isinstance(answer, list) or not answer or any(not isinstance(n, str) for n in answer) or len(set(answer)) != len(answer) or any(n not in c.presets(state['language']) for n in answer):
                    raise c.Error('Choose one or more unique role IDs from the listed choices')
                state['selected'] = answer
            elif step.endswith('.model'):
                if not isinstance(answer, str) or not answer.strip() or '\n' in answer or len(answer) > 200: raise c.Error('Invalid model identifier')
                name = current['role']; state['roles'][name] = dict(c.presets(state['language'])[name], model=answer)
            elif step.endswith('.effort'):
                if answer not in hosts.efforts(): raise c.Error('Select a listed effort value')
                state['roles'][current['role']]['effort'] = answer
            elif step == 'confirm':
                if not isinstance(answer, bool): raise c.Error('Confirmation must be true or false')
                if answer:
                    if not apply: raise c.Error('Confirmation requires --apply')
                    # Serialize config writes and recheck approval against current config.
                    target = c.path(scope, project)
                    with c.lock(c.path('global', project)) if scope == 'project' else _nullcontext():
                        with c.lock(target):
                            plan = preview(state, scope, project)
                            if token != plan['confirmation_token']: raise c.Error('Configuration changed or confirmation token missing; review init preview again')
                            cfg = c.read(target)
                            for name, role in plan['add'].items():
                                c.validate_name(name); c.validate_view(role)
                                cfg['roles'][name] = hosts.with_view(cfg['roles'].get(name), role)
                            c.save(target, cfg)
                    state['completed'] = True
            else: raise c.Error('Initialization is complete; use --reset --apply to start another draft')
            state['revision'] += 1
        c.atomic(p, json.dumps(state, ensure_ascii=False, indent=2) + '\n')
        return view(state, scope, project)

from contextlib import nullcontext as _nullcontext
