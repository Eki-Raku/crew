"""Host selection. Codex and Claude Code keep separate homes, role settings and tasks.

A config file always uses the same layout: top-level role fields belong to Codex,
``hosts.claude`` belongs to Claude Code. Neither host falls back to the other.
"""
import json
import os
from pathlib import Path
import re
import tomllib

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
    return json.dumps(value, ensure_ascii=False).replace('\x7f', '\\u007f')


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
                           ('model_reasoning_effort', role['effort']), ('developer_instructions', role['instructions'])]:
            text += key + ' = ' + _quote(value) + '\n'
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
        return all(obj.get(k) == v for k, v in [('name', cls.agent_name(name)), ('model', role['model']),
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
