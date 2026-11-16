"""Host selection. Codex and Claude Code keep separate homes, role settings and tasks.

A config file always uses the same layout: top-level role fields belong to Codex,
``hosts.claude`` belongs to Claude Code. Neither host falls back to the other.
"""
import json
import os
from pathlib import Path
import re
import tomllib

from . import i18n

HOSTS = ('codex', 'claude')
LABELS = {'codex': 'Codex', 'claude': 'Claude Code'}
EFFORTS = {'codex': ('minimal', 'low', 'medium', 'high', 'xhigh'),
           'claude': ('low', 'medium', 'high', 'xhigh', 'max')}
ALL_EFFORTS = tuple(dict.fromkeys(e for host in HOSTS for e in EFFORTS[host]))
MARK = '# Managed by Crew.'
HEADER = MARK + ' Edit source configuration, then export.'

CURRENT = 'codex'
SOURCE = 'default'      # flag | CREW_HOST | environment | default
DETECTED = (None, None)  # (host, evidence variable)


def detect(env=None):
    """Identify the running host from its environment, or (None, None).

    Codex also sets CLAUDE_PLUGIN_ROOT for plugin compatibility, so its own
    PLUGIN_ROOT is checked before any Claude Code marker.
    """
    env = os.environ if env is None else env
    if env.get('PLUGIN_ROOT'): return 'codex', 'PLUGIN_ROOT'
    for name in ('CLAUDE_PROJECT_DIR', 'CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_PLUGIN_ROOT'):
        if env.get(name): return 'claude', name
    return None, None


def select(explicit=None, env=None):
    """Choose the host for this process: flag, CREW_HOST, environment, then Codex."""
    global CURRENT, SOURCE, DETECTED
    env = os.environ if env is None else env
    DETECTED = detect(env)
    configured = env.get('CREW_HOST', '').strip().lower()
    if explicit in HOSTS: CURRENT, SOURCE = explicit, 'flag'
    elif configured in HOSTS: CURRENT, SOURCE = configured, 'CREW_HOST'
    elif DETECTED[0]: CURRENT, SOURCE = DETECTED[0], 'environment'
    else: CURRENT, SOURCE = 'codex', 'default'
    return CURRENT


def mismatch():
    """The running environment when it contradicts the selected host, else None."""
    return DETECTED[0] if DETECTED[0] and DETECTED[0] != CURRENT else None


def label(host=None): return LABELS[host or CURRENT]
def efforts(host=None): return EFFORTS[host or CURRENT]


ROLE_LABELS = {
    'zh': {
        'coordinator': '协调者',
        'implementer': '实施者',
        'reviewer': '审查者',
        'researcher': '研究者',
        'tester': '测试者',
        'documenter': '文档记录员',
    },
    'en': {
        'coordinator': 'coordinator',
        'implementer': 'implementer',
        'reviewer': 'reviewer',
        'researcher': 'researcher',
        'tester': 'tester',
        'documenter': 'documenter',
    },
}


def role_label(role_id, language=None):
    """Localized display name for built-in roles; custom roles keep their role_id."""
    lang = 'zh' if (language or i18n.LANG) == 'zh' else 'en'
    return ROLE_LABELS[lang].get(role_id, role_id)


NAME_OPEN, NAME_CLOSE = '【', '】'  # Crew-side canonical display brackets (Codex rejects ASCII brackets in native names).


def subagent_name(role_id, model, language=None, index=None, total=1):
    """Canonical Crew display name: ``【model】role`` (``#n`` appended when parallel)."""
    base = f'{NAME_OPEN}{model}{NAME_CLOSE}{role_label(role_id, language)}'
    if total and total > 1 and index is not None:
        return f'{base}#{index}'
    return base


def _slug(text, limit=40):
    slug = re.sub(r'[^a-z0-9]+', '_', str(text).lower()).strip('_')[:limit].strip('_')
    return slug or 'x'


def task_name(role_id, model, index=None, seq=1):
    """Deterministic Codex ``task_name`` (spawn_agent accepts only lowercase letters, digits, underscores).

    ``seq`` > 1 appends ``_r<seq>`` so a later dispatch never collides with an agent
    from an earlier dispatch that is still open (Codex rejects duplicate agent paths).
    """
    name = _slug(model) + '_' + _slug(role_id)
    if index is not None: name += f'_{int(index)}'
    if seq and seq > 1: name += f'_r{int(seq)}'
    return name


NICKNAME_RULE = re.compile(r'^[A-Za-z0-9 _-]+$')  # Codex 0.169.0: ASCII letters, digits, spaces, hyphens, underscores


def native_nicknames(role_id, model, max_instances=1):
    """Native Codex nickname_candidates for a role, or [] when no valid ASCII form exists.

    Codex picks one candidate when it spawns the role and offers no way for the caller to choose,
    so a role allowing ``n`` parallel instances gets ``n`` numbered candidates.
    """
    model_part = re.sub(r'-{2,}', '-', re.sub(r'[^A-Za-z0-9 _-]+', '-', str(model))).strip(' -')
    role_part = re.sub(r'[^A-Za-z0-9_-]+', '-', str(role_id)).strip(' -')
    if not model_part or not role_part: return []
    base = f'{model_part} {role_part}'
    names = [base] if not max_instances or max_instances <= 1 else [f'{base} {i}' for i in range(1, max_instances + 1)]
    return names if all(NICKNAME_RULE.match(n) for n in names) and len(set(names)) == len(names) else []


def home(host=None):
    host = host or CURRENT
    if host == 'claude':
        return Path(os.environ.get('CLAUDE_CONFIG_DIR') or str(Path.home() / '.claude')).expanduser().resolve()
    return Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))).expanduser().resolve()


def describe():
    return {'host': CURRENT, 'host_label': label(), 'host_source': SOURCE,
            'detected_environment': DETECTED[0], 'detected_by': DETECTED[1]}


# ---- role views: one independent definition per host inside a role entry ----

def view(entry, host=None):
    """This host's definition inside a stored role entry, or None when absent."""
    host = host or CURRENT
    if not isinstance(entry, dict): return None
    if host == 'codex':
        own = {k: v for k, v in entry.items() if k != 'hosts'}
        return own or None
    other = entry.get('hosts')
    return other.get(host) if isinstance(other, dict) and isinstance(other.get(host), dict) else None


def with_view(entry, value, host=None):
    """Return the entry with this host's definition replaced; None removes it.

    The result is None when no host has a definition left.
    """
    host = host or CURRENT
    others = dict((entry or {}).get('hosts') or {})
    if host == 'codex':
        result = dict(value or {})
    else:
        result = {k: v for k, v in (entry or {}).items() if k != 'hosts'}
        if value is None: others.pop(host, None)
        else: others[host] = dict(value)
    if others: result['hosts'] = others
    return result or None


def views(config, host=None):
    return {name: v for name, entry in config['roles'].items() if (v := view(entry, host)) is not None}


def enabled(config, host=None):
    """This host's stored enabled flag, or None when the file does not set it."""
    host = host or CURRENT
    if host == 'codex': return config.get('enabled')
    return ((config.get('hosts') or {}).get(host) or {}).get('enabled')


def with_enabled(config, value, host=None):
    host = host or CURRENT
    result = dict(config)
    if host == 'codex': result['enabled'] = value
    else:
        section = dict(result.get('hosts') or {})
        section[host] = dict(section.get(host) or {}, enabled=value)
        result['hosts'] = section
    return result


# ---- exported agent files ----

def _quote(value):
    # JSON and TOML basic strings differ at DEL; escape it rather than emit an
    # illegal literal. Keep Unicode readable and preserve the original value.
    text = json.dumps(value, ensure_ascii=False).replace('\x7f', '\\u007f')
    # YAML 1.1 parsers treat these as line breaks inside quoted scalars.
    return text.replace('\u0085', '\\u0085').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')


class CodexExport:
    project_dir = ('.codex', 'agents')
    pattern = 'crew-*.toml'
    all_files = '*.toml'

    @staticmethod
    def filename(name): return 'crew-' + name + '.toml'
    @staticmethod
    def agent_name(name): return 'crew_' + name
    @staticmethod
    def role_id(path): return path.stem[len('crew-'):]
    @staticmethod
    def managed(text): return text.startswith(MARK)

    @classmethod
    def render(cls, name, role):
        text = HEADER + '\n'
        for key, value in [('name', cls.agent_name(name)), ('description', role['description']), ('model', role['model']),
                           ('model_reasoning_effort', role['effort'])]:
            text += key + ' = ' + _quote(value) + '\n'
        nicknames = native_nicknames(name, role['model'], role.get('max_instances', 1))
        # ASCII-only by Codex's own validation; an invalid list would make the whole role file unloadable.
        if nicknames: text += 'nickname_candidates = [' + ', '.join(_quote(n) for n in nicknames) + ']\n'
        text += 'developer_instructions = ' + _quote(role['instructions']) + '\n'
        tomllib.loads(text)  # Never report a successful export of invalid TOML.
        return text

    @staticmethod
    def declared_name(text):
        """Agent name declared by an existing file; ValueError when unreadable."""
        return tomllib.loads(text).get('name')

    @classmethod
    def in_sync(cls, text, name, role):
        try: obj = tomllib.loads(text)
        except ValueError: return False
        expected = native_nicknames(name, role['model'], role.get('max_instances', 1)) or None
        return obj.get('nickname_candidates') == expected and all(obj.get(k) == v for k, v in [('name', cls.agent_name(name)), ('model', role['model']),
                   ('model_reasoning_effort', role['effort']), ('description', role['description']),
                   ('developer_instructions', role['instructions'])])


class ClaudeExport:
    project_dir = ('.claude', 'agents')
    pattern = 'crew-*.md'
    all_files = '*.md'

    @staticmethod
    def filename(name): return 'crew-' + name + '.md'
    @staticmethod
    def agent_name(name): return 'crew-' + name
    @staticmethod
    def role_id(path): return path.stem[len('crew-'):]
    @staticmethod
    def managed(text): return text.startswith('---\n' + MARK)

    @classmethod
    def render(cls, name, role):
        # JSON strings are valid YAML double-quoted scalars; the body is the prompt verbatim.
        body = role['instructions']
        return ('---\n' + HEADER + '\n'
                + 'name: ' + cls.agent_name(name) + '\n'
                + 'description: ' + _quote(role['description']) + '\n'
                + 'model: ' + _quote(role['model']) + '\n'
                + 'effort: ' + role['effort'] + '\n'
                + '---\n' + body + ('' if not body or body.endswith('\n') else '\n'))

    @staticmethod
    def declared_name(text):
        if not text.startswith('---'): return None
        end = text.find('\n---', 3)
        found = re.search(r'^name:[ \t]*(.+?)[ \t]*$', text[3:end if end != -1 else len(text)], re.M)
        return found.group(1).strip('"\'') if found else None

    @classmethod
    def in_sync(cls, text, name, role): return text == cls.render(name, role)


EXPORTS = {'codex': CodexExport, 'claude': ClaudeExport}
def export(host=None): return EXPORTS[host or CURRENT]
