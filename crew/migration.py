"""Explicit, additive migration from RolePilot. Legacy files remain untouched."""
from pathlib import Path
from . import core as c
from . import hosts


def migration_root(project):
    """Recognize legacy-only projects, including calls from subdirectories."""
    p = Path(project).expanduser().resolve()
    for candidate in (p, *p.parents):
        if any((candidate / part).exists() for part in ('.crew/onboarding.json', '.crew/config.json', '.rolepilot/config.json', '.git')):
            return candidate
    return p


def _safe_destination(path):
    # Do not replace user symlinks or write through linked configuration folders.
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise c.Error(f'Migration destination is a symlink: {candidate}')
    if path.exists() and not path.is_file():
        raise c.Error(f'Migration destination is not a file: {path}')


def _plan(source, target, scope):
    legacy, current = c.read(source), c.read(target)
    merged = dict(current, roles=dict(current['roles']))
    added, preserved, conflicts = [], [], []
    for name, role in legacy['roles'].items():
        if name in current['roles']:
            preserved.append(name)
            if current['roles'][name] != role: conflicts.append(name)
        else:
            merged['roles'][name] = role
            added.append(name)
    enabled_added = 'enabled' in legacy and 'enabled' not in current
    if enabled_added: merged['enabled'] = legacy['enabled']
    enabled_conflict = 'enabled' in legacy and 'enabled' in current and legacy['enabled'] != current['enabled']
    return merged, {
        'scope': scope, 'source': str(source), 'target': str(target),
        'source_exists': source.exists(), 'add_roles': sorted(added),
        'preserved_existing': sorted(preserved), 'conflicts': sorted(conflicts),
        'enabled_added': enabled_added, 'enabled_conflict': enabled_conflict,
        'changed': merged != current, 'applied': False,
    }


def legacy_inventory(project):
    project = migration_root(project)
    exports = []
    for scope, directory in [('global', c.home() / 'agents'), ('project', project / '.codex' / 'agents')]:
        for p in sorted(directory.glob('rolepilot-*.toml')):
            exports.append({'scope': scope, 'path': str(p), 'managed': p.read_text().startswith('# Managed by RolePilot.')})
    entries = []
    for p in (c.home() / 'AGENTS.md', c.home() / 'AGENTS.override.md'):
        if p.is_file() and ('<!-- rolepilot:start -->' in p.read_text() or '<!-- rolepilot:end -->' in p.read_text()):
            entries.append(str(p))
    return {
        'legacy_exports': exports, 'legacy_agents_entries': entries,
        'legacy_data_retained': [str(c.home() / 'rolepilot'), str(project / '.rolepilot')],
        'next_steps': [
            'Review migrated Crew configuration and export it using crew export, then crew export --apply.',
            'Disable the old RolePilot plugin in the host before enabling Crew hooks, to avoid duplicate events.',
            'Using the old RolePilot CLI, preview rolepilot export --clean --project <project>; apply with --apply after checking the listed files. Repeat with --scope global if global exports are listed.',
            'Using the old RolePilot CLI, preview rolepilot uninstall; apply with --apply to remove only its marked AGENTS blocks. Then preview crew setup and apply the new entry.',
            'Keep legacy configuration and receipts as a backup. Migration does not delete or move them.',
        ],
    }


def migrate(project, scope='all', apply=False):
    if scope not in ('all', 'global', 'project'): raise c.Error('Invalid migration scope')
    if hosts.CURRENT != 'codex': raise c.Error('RolePilot migration applies to Codex only; rerun with --host codex')
    project = migration_root(project)
    locations = {
        'global': (c.home() / 'rolepilot' / 'config.json', c.home() / 'crew' / 'config.json'),
        'project': (project / '.rolepilot' / 'config.json', project / '.crew' / 'config.json'),
    }
    selected = list(locations) if scope == 'all' else [scope]
    # Validate all inputs before making any write; subsequent file failures are reported
    # by the caller and retry is additive/idempotent.
    for name in selected:
        source, target = locations[name]
        _safe_destination(target)
        _plan(source, target, name)
    results = []
    for name in selected:
        source, target = locations[name]
        if apply:
            with c.lock(target):
                _safe_destination(target)
                merged, result = _plan(source, target, name)
                if result['changed']:
                    c.save(target, merged)
                    result['applied'] = True
        else:
            _, result = _plan(source, target, name)
        results.append(result)
    return {'operation': 'migrate_rolepilot', 'project': str(project), 'apply_requested': apply,
            'configurations': results, **legacy_inventory(project)}
