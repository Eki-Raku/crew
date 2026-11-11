import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
import tomllib
try:
    import fcntl
except ImportError:
    fcntl = None

from .presets import get_presets
from . import hosts
from . import i18n

def presets(language=None):
    return get_presets(language or i18n.LANG)

SCHEMA = 1
EFFORTS = hosts.EFFORTS['codex']  # Codex values; use hosts.efforts() for the selected host
START = '<!-- crew:start -->'
END = '<!-- crew:end -->'

class Error(Exception):
    pass

def home():
    # Each host keeps its own global configuration, receipts and task store.
    return hosts.home()

def project_root(project):
    p = Path(project).expanduser().resolve()
    for candidate in (p, *p.parents):
        if (candidate / '.crew' / 'onboarding.json').exists() or (candidate / '.crew' / 'onboarding.claude.json').exists() or (candidate / '.rolepilot' / 'config.json').exists() or (candidate / '.crew' / 'config.json').exists() or (candidate / '.git').exists(): return candidate
    return p

def path(scope, project):
    return (home() / 'crew' if scope == 'global' else project_root(project) / '.crew') / 'config.json'

def empty():
    return {'schema_version': SCHEMA, 'roles': {}}

def read(p):
    if not p.exists(): return empty()
    try: data = json.loads(p.read_text())
    except (ValueError, OSError) as e: raise Error(f'Cannot read {p}: {e}')
    if not isinstance(data, dict) or data.get('schema_version') != SCHEMA:
        raise Error(f'Unsupported configuration schema: {p}; keep a backup before migrating')
    if not isinstance(data.get('roles'), dict): raise Error(f'Invalid roles in {p}')
    if 'enabled' in data and not isinstance(data['enabled'], bool): raise Error('enabled must be boolean')
    sections = data.get('hosts', {})
    if not isinstance(sections, dict) or any(k not in hosts.HOSTS[1:] or not isinstance(v, dict) for k, v in sections.items()): raise Error('Invalid hosts section in ' + str(p))
    if any('enabled' in v and not isinstance(v['enabled'], bool) for v in sections.values()): raise Error('enabled must be boolean')
    for name, value in data['roles'].items(): validate_role(name, value)
    return data

@contextlib.contextmanager
def lock(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    lp = Path(str(p) + '.lock')
    start = time.monotonic()
    while True:
        try:
            fd = os.open(lp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(fd, str(os.getpid()).encode()); os.close(fd); break
        except FileExistsError:
            if _release_dead_lock(lp): continue
            if time.monotonic() - start > 2: raise Error(f'Busy or stale lock: {lp}; inspect before removing')
            time.sleep(.05)
    try: yield
    finally: lp.unlink(missing_ok=True)

def _release_dead_lock(lp):
    # Reclaimers must serialize on the stale inode. Once its path is reused,
    # a reclaimer that still holds the old file must leave the new one alone.
    if fcntl is None:
        return False
    try:
        with lp.open() as stale:
            fcntl.flock(stale, fcntl.LOCK_EX | fcntl.LOCK_NB)
            pid = int(stale.read())
            if pid <= 0:
                return False
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                pass
            else:
                return False
            if not os.path.samestat(os.fstat(stale.fileno()), lp.stat()):
                return False
            lp.unlink()
    except (OSError, ValueError):
        return False
    return True

def atomic(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + p.name, dir=p.parent)
    try:
        with os.fdopen(fd, 'w') as f: f.write(text); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def save(p, data): atomic(p, json.dumps(data, ensure_ascii=False, indent=2) + '\n')

def validate_view(value, host=None):
    """Validate one host's role definition (the flat form used by wizard and exports)."""
    host = host or hosts.CURRENT
    if not isinstance(value, dict): raise Error('Role must be an object')
    if value.get('disabled') is True and len(value) == 1: return
    for key in ('model', 'description'):
        if not isinstance(value.get(key), str) or not value[key].strip(): raise Error(f'Missing role field: {key}')
    if not isinstance(value.get('instructions'), str): raise Error('instructions must be a string; use an empty string for an empty agent')
    if '\n' in value['model'] or len(value['model']) > 200: raise Error('Invalid model identifier')
    if value.get('effort') not in hosts.EFFORTS[host]: raise Error(f'effort must be one of {hosts.EFFORTS[host]}')
    if 'disabled' in value and not isinstance(value['disabled'], bool): raise Error('disabled must be boolean')

def validate_name(name):
    if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,47}', name): raise Error('Role id must match [a-z][a-z0-9_-]{0,47}')

def validate_role(name, value):
    """Validate a stored role entry: top-level fields are Codex, hosts.<name> other hosts."""
    validate_name(name)
    if not isinstance(value, dict): raise Error('Role must be an object')
    others = value.get('hosts', {})
    if not isinstance(others, dict) or any(k not in hosts.HOSTS[1:] or not isinstance(v, dict) for k, v in others.items()):
        raise Error('hosts must map a supported host (claude) to its role settings; Codex settings stay at the top level')
    defined = [h for h in hosts.HOSTS if hosts.view(value, h) is not None]
    if not defined: raise Error('Role has no settings for any host')
    for host in defined: validate_view(hosts.view(value, host), host)

def effective(project, host=None):
    host = host or hosts.CURRENT
    global_cfg, local = read(path('global', project)), read(path('project', project))
    roles = {}
    for scope, cfg in [('global', global_cfg), ('project', local)]:
        for name, val in hosts.views(cfg, host).items(): roles[name] = dict(val, source=scope, prompt_state='empty_agent' if not val.get('instructions', '').strip() else 'configured')
    local_enabled, global_enabled = hosts.enabled(local, host), hosts.enabled(global_cfg, host)
    return {'schema_version': SCHEMA, 'host': host, 'enabled': local_enabled if local_enabled is not None else (global_enabled if global_enabled is not None else True),
            'enabled_source': 'project' if local_enabled is not None else 'global', 'roles': roles}

def other_host_roles(project, host=None):
    """Role ids this project's files define for another host but not for this one."""
    host = host or hosts.CURRENT
    mine = effective(project, host)['roles']
    names = set()
    for cfg in (read(path('global', project)), read(path('project', project))):
        names.update(n for n, entry in cfg['roles'].items() if n not in mine and any(hosts.view(entry, h) is not None for h in hosts.HOSTS if h != host))
    return sorted(names)

def role_write(scope, project, name, value, operation):
    """Change the selected host's definition of a role; other hosts' settings are untouched."""
    host = hosts.CURRENT
    validate_name(name)
    p = path(scope, project)
    with lock(p):
        cfg = read(p)
        current = hosts.view(cfg['roles'].get(name), host)
        if operation == 'delete':
            if current is None: raise Error('Role not present in this scope; use disable to mask an inherited role')
            new = None
        elif operation == 'disable':
            new = dict(current or {}, disabled=True)
        elif operation == 'enable':
            if current is None: raise Error('No role in this scope to enable')
            if set(current) == {'disabled'}:
                inherited = hosts.view(read(path('global', project))['roles'].get(name), host) if scope == 'project' else None
                if not inherited or inherited.get('disabled'):
                    raise Error('Role definition missing; update its model, effort, description and instructions before enabling')
                new = None  # Remove the mask and resume inheritance.
            else:
                new = dict(current, disabled=False)
                validate_view(new, host)
        else:
            if operation == 'create' and current is not None: raise Error('Role already exists; use update')
            if operation == 'update' and current is None: raise Error('No role in this scope; use create for an override')
            new = dict(current, **{k: v for k, v in value.items() if v is not None}) if operation == 'update' else dict(value)
            validate_view(new, host)
        entry = hosts.with_view(cfg['roles'].get(name), new, host)
        if entry is None: cfg['roles'].pop(name, None)
        else: cfg['roles'][name] = entry
        save(p, cfg)
    result = {'saved': str(p), 'role': name, 'operation': operation, 'host': host, 'next': 'Preview export, then export --apply to synchronize host role files.'}
    if new and 'instructions' in new: result['prompt_state'] = 'configured' if new['instructions'].strip() else 'empty_agent'
    return result

def set_enabled(scope, project, enabled):
    p = path(scope, project)
    with lock(p):
        cfg = hosts.with_enabled(read(p), enabled); save(p, cfg)
    return {'saved': str(p), 'enabled': enabled, 'host': hosts.CURRENT}

def agents_target():
    override = home() / 'AGENTS.override.md'
    return override if override.exists() and override.stat().st_size else home() / 'AGENTS.md'

def entry_remove(text):
    if START not in text and END not in text: return text
    if text.count(START) != 1 or text.count(END) != 1 or text.index(START) > text.index(END):
        raise Error('Malformed Crew marker; refusing to edit')
    begin = text.index(START); end = text.index(END) + len(END)
    if text[end:end+1] == '\n': end += 1
    return text[:begin] + text[end:]

NO_ENTRY = 'Claude Code needs no global entry: the SessionStart hook already delivers the Crew status each session, and CLAUDE.md is left untouched.'

def setup(apply=False):
    if hosts.CURRENT != 'codex': return {'host': hosts.CURRENT, 'status': 'not_applicable', 'changed': False, 'applied': False, 'note': NO_ENTRY}
    p = agents_target()
    command = str(Path(__file__).resolve().parents[1] / 'bin' / 'crew')
    line = f'Before project work, run the Crew CLI at {json.dumps(command)} with `preflight --project <project-path> --json`; if setup is needed, present its questions and apply the user answers. Respect project disablement.'
    with lock(p):
        old = p.read_text() if p.exists() else ''
        clean = entry_remove(old)
        new = clean + ('' if not clean or clean.endswith('\n') else '\n') + START + '\n' + line + '\n' + END + '\n'
        if apply and new != old:
            if p.exists(): atomic(Path(str(p) + '.crew-backup-' + str(time.time_ns())), old)
            atomic(p, new)
    return {'target': str(p), 'entry': line, 'changed': old != new, 'applied': apply,
            'next': 'Configure roles, then export them. This command does not install or enable the host plugin.'}

def uninstall(apply=False):
    if hosts.CURRENT != 'codex': return {'host': hosts.CURRENT, 'status': 'not_applicable', 'entry_removed_from': [], 'applied': False, 'note': NO_ENTRY}
    changed = []
    for p in (home() / 'AGENTS.md', home() / 'AGENTS.override.md'):
        if not p.exists(): continue
        with lock(p):
            old = p.read_text(); new = entry_remove(old)
            if old != new:
                changed.append(str(p))
                if apply: atomic(p, new)
    return {'entry_removed_from': changed, 'applied': apply, 'note': 'Config, receipts and exported agents are retained; remove exports using export --clean --apply.'}

def receipt_path(project):
    return home() / 'crew' / 'receipts' / (hashlib.sha256(str(project_root(project)).encode()).hexdigest()[:20] + '.json')

def record(event, project, simulated=False):
    # Intentionally never persist conversation content, tool inputs or secrets.
    name = event.get('hook_event_name', event.get('event', 'Unknown'))
    p = receipt_path(project)
    with lock(p):
        data = json.loads(p.read_text()) if p.exists() else {'events': [], 'auto_compactions': {}}
        sid = str(event.get('session_id', 'unknown'))[:200]
        row = {'event': str(name)[:80], 'session_id': sid, 'time': dt.datetime.now(dt.timezone.utc).isoformat(),
               'simulated': simulated, 'trigger': str(event.get('trigger', ''))[:30]}
        data['events'] = (data['events'] + [row])[-100:]
        if name == 'PostCompact' and event.get('trigger') == 'auto' and not simulated:
            data['auto_compactions'][sid] = data['auto_compactions'].get(sid, 0) + 1
            data['auto_compactions'] = dict(list(data['auto_compactions'].items())[-100:])
        save(p, data)
    return row

def questions():
    return [{'id': 'roles', 'type': 'roles', 'question': 'Which roles do you need?', 'suggestions': list(presets()), 'presets': presets()},
            {'id': 'model', 'per_role': True, 'question': 'Choose an available model ID from your Codex model picker.' if hosts.CURRENT == 'codex' else 'Choose a Claude Code model: a full model ID, an alias such as sonnet or opus, or inherit.'},
            {'id': 'effort', 'per_role': True, 'choices': list(hosts.efforts()), 'question': 'Choose an effort supported by that model.'}]

def export_dir(project, scope='project'):
    return home() / 'agents' if scope == 'global' else project_root(project).joinpath(*hosts.export().project_dir)

def preflight(project):
    host, spec = hosts.CURRENT, hosts.export()
    cfg = effective(project)
    active = [k for k, v in cfg['roles'].items() if not v.get('disabled')]
    drift = []
    dest = export_dir(project)
    expected = active if cfg['enabled'] else []
    stale = []
    for p in dest.glob(spec.pattern):
        if spec.managed(p.read_text()):
            name = spec.role_id(p)
            if name not in expected: stale.append(name)
    drift.extend(sorted(stale))
    for name in expected:
        try:
            if not spec.in_sync((dest / spec.filename(name)).read_text(), name, cfg['roles'][name]): drift.append(name)
        except (OSError, ValueError): drift.append(name)
    legacy = host == 'codex' and ((home() / 'rolepilot' / 'config.json').exists() or (project_root(project) / '.rolepilot' / 'config.json').exists())
    return {'host': host, 'legacy_config_detected': legacy, 'migration_command': 'crew migrate' if legacy else None, 'status': 'needs_export' if drift else ('disabled' if not cfg['enabled'] else ('ready' if active else 'needs_setup')), 'enabled': cfg['enabled'], 'export_drift': drift,
            'stale_exports': sorted(stale), 'next': 'Preview export, then export --apply to synchronize project role files.' if drift else None,
            'project': str(project_root(project)), 'roles': active, 'roles_for_other_hosts_only': other_host_roles(project),
            'questions': [] if active or not cfg['enabled'] else questions(),
            'host_plugin_enabled': 'unknown', 'model_availability': 'not_live_verified'}

def doctor(project):
    cfg = effective(project); p = receipt_path(project)
    events = json.loads(p.read_text()) if p.exists() else {'events': [], 'auto_compactions': {}}
    result = dict(hosts.describe(), preflight=preflight(project), effective=cfg, home=str(home()), hook_receipts=events)
    if hosts.CURRENT == 'codex':
        target = agents_target()
        result.update(codex_home=str(home()), agents_file=str(target), entry_present=target.exists() and START in target.read_text())
        client = 'Host enablement and model entitlement require the actual Codex client.'
    else:
        client = 'Plugin enablement and model access require the actual Claude Code client.'
    result['limitations'] = [client, 'A receipt is historical evidence, not proof the hook is still enabled.', 'Role changes affect future agents, not running agents.', 'Project disablement does not remove global agent exports; prefer project-only export.']
    return result

def export_agents(project, scope='project', apply=False, clean=False):
    host, spec = hosts.CURRENT, hosts.export()
    cfg = effective(project)
    dest = export_dir(project, scope)
    # Global export must never copy project overrides into global agents.
    if scope == 'global':
        only = read(path('global', project)); flag = hosts.enabled(only)
        cfg = {'roles': hosts.views(only), 'enabled': True if flag is None else flag}
    active = {name: role for name, role in cfg['roles'].items() if not role.get('disabled')}
    expected = {}
    if cfg.get('enabled', True) and not clean:
        for name, role in active.items(): expected[spec.filename(name)] = spec.render(name, role)
    result = {'host': host, 'directory': str(dest), 'write': list(expected), 'remove': [], 'applied': apply}
    skipped = other_host_roles(project) if scope == 'project' else []
    if skipped: result['skipped_not_configured_for_host'] = skipped
    first = not dest.exists()
    if not apply and first: return result
    with lock(dest / '.crew-export'):
        reserved = {spec.agent_name(n) for n in active}
        for p in dest.glob(spec.all_files):
            text = p.read_text()
            if p.name in expected and spec.managed(text): continue
            try: declared = spec.declared_name(text)
            except ValueError: raise Error(f'Invalid existing agent TOML: {p}')
            if declared in reserved and not spec.managed(text):
                raise Error(f'Agent name collision: {p}')
        old = list(dest.glob(spec.pattern))
        for p in old:
            if not spec.managed(p.read_text()):
                raise Error(f'Unmanaged file collision: {p}')
        for p in old:
            if p.name not in expected: result['remove'].append(p.name)
        if apply:
            for name, text in expected.items(): atomic(dest / name, text)
            for name in result['remove']: (dest / name).unlink()
    if apply and first and expected and host == 'claude':
        result['note'] = 'This agents directory is new. Claude Code only watches agent directories that existed when the session started; restart the session to load these roles.'
    return result


def install_presets(scope, project, names, model, effort, language=None):
    templates = presets(language)
    unknown = set(names) - set(templates)
    if unknown: raise Error('Unknown presets: ' + ', '.join(sorted(unknown)))
    p = path(scope, project)
    with lock(p):
        cfg = read(p)
        # Project imports preserve both local definitions and inherited global roles.
        existing = set(hosts.views(cfg))
        if scope == 'project': existing.update(hosts.views(read(path('global', project))))
        added, skipped = [], []
        for name in names:
            if name in existing: skipped.append(name); continue
            role = dict(templates[name], model=model, effort=effort)
            validate_view(role); cfg['roles'][name] = hosts.with_view(cfg['roles'].get(name), role); added.append(name)
        save(p, cfg)
    return {'added': added, 'skipped_existing': skipped, 'saved': str(p), 'host': hosts.CURRENT, 'next': 'Review/edit prompts and model choices, then preview export.'}
