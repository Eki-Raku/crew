"""Small, transactional shared task context, never a conversation/transcript store.

CAS versions are monotonic, including lifecycle changes. ``read(since=...)``
returns a coalesced field delta, not an event log: apply ``changes`` then remove
``removed`` and retain the returned version only after consuming the response.
Manual deletion is recoverable trash, and only explicit ``restore`` can recover
its content. Session continuation after deletion creates a different empty task.
"""
import contextlib
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import uuid

from . import core
from . import hosts

SCHEMA_VERSION = 1
STATE_FIELDS = frozenset(('summary', 'goal', 'constraints', 'decisions', 'progress',
                          'next', 'blockers', 'evidence'))
MAX_STATE_BYTES = 16 * 1024
MAX_FIELD_CHARS = 4096
MAX_LIST_ITEMS = 64
MAX_ITEM_CHARS = 1024
MAX_REQUESTS_PER_TASK = 128
SOFT_TASK_CAPACITY = 2000
CLOSED_STATUSES = frozenset(('archived', 'deleted'))
SESSION_STATUSES = CLOSED_STATUSES | {'active', 'unknown'}


class ContextError(core.Error):
    pass


class ConflictError(ContextError):
    def __init__(self, expected, current):
        self.expected_version = expected
        self.current_version = current
        super().__init__(f'Context version conflict: expected {expected}, current {current}; read and reconcile before retrying')


class NotFoundError(ContextError):
    pass


class DeletedTaskError(ContextError):
    pass


class StaleAssociationError(ContextError):
    pass


class IdempotencyError(ContextError):
    pass


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _identifier(value, name, maximum=256):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ContextError(f'{name} must be a nonempty string of at most {maximum} characters without control characters')
    return value


def _version(value):
    if type(value) is not int or value < 0:
        raise ContextError('Version must be a nonnegative integer')
    return value


def _state(value, allow_removed=False):
    if not isinstance(value, dict) or not set(value).issubset(STATE_FIELDS):
        raise ContextError('Context must be an object containing only: ' + ', '.join(sorted(STATE_FIELDS)))
    for key, item in value.items():
        if item is None and allow_removed:
            continue
        if isinstance(item, str) and len(item) <= MAX_FIELD_CHARS:
            continue
        if isinstance(item, list) and len(item) <= MAX_LIST_ITEMS and all(isinstance(v, str) and len(v) <= MAX_ITEM_CHARS for v in item):
            continue
        raise ContextError(f'{key} must be a short string (up to {MAX_FIELD_CHARS} characters) or at most {MAX_LIST_ITEMS} strings of {MAX_ITEM_CHARS} characters')
    if len(_json(value).encode('utf-8')) > MAX_STATE_BYTES:
        raise ContextError(f'Task context exceeds {MAX_STATE_BYTES} bytes; checkpoint a smaller summary')
    # Isolate caller-owned mutable lists before entering a transaction.
    return json.loads(_json(value))


def _timestamp(value=None):
    if value is None:
        return time.time()
    if isinstance(value, str):
        try:
            parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                raise ValueError('Timezone is required')
            value = parsed.timestamp()
        except (ValueError, OverflowError) as exc:
            raise ContextError('observed_at must be an ISO timestamp with a timezone or epoch seconds') from exc
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ContextError('observed_at must be a finite nonnegative timestamp')
    return float(value)


def project_scope(project):
    """Resolve worktrees to their Git common directory without reading sessions.

    A non-Git project uses core's project-root markers. Ignore inherited Git
    directory overrides so one repository cannot silently select another scope.
    """
    root = core.project_root(project)
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    try:
        result = subprocess.run(['git', '-C', str(root), 'rev-parse', '--git-common-dir'],
                                capture_output=True, text=True, timeout=5, env=env)
        if result.returncode == 0 and result.stdout.strip():
            common = Path(result.stdout.strip())
            common = (common if common.is_absolute() else root / common).resolve()
            identity = 'git:' + str(common)
        else:
            identity = 'directory:' + str(root)
    except (OSError, subprocess.TimeoutExpired):
        identity = 'directory:' + str(root)
    return hashlib.sha256(identity.encode()).hexdigest(), identity


class ContextStore:
    def __init__(self, project, data_home=None):
        self.project = Path(project).expanduser().resolve()
        self.scope_id, self.scope_identity = project_scope(project)
        selected = data_home if data_home is not None else os.environ.get('CREW_DATA_HOME')
        self.data_home = Path(selected).expanduser().resolve() if selected else core.home() / 'crew'
        self.data_home.mkdir(parents=True, exist_ok=True, mode=0o700)
        filename = 'context.sqlite3' if hosts.CURRENT == 'codex' else 'context.claude.sqlite3'
        self.path = self.data_home / filename
        # New files are private from creation; never change existing permissions.
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        try:
            self._initialize()
        except sqlite3.Error as exc:
            raise ContextError(f'Cannot initialize context database: {exc}') from exc

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('PRAGMA busy_timeout=10000')
        return conn

    def _initialize(self):
        with contextlib.closing(self._connect()) as conn:
            existing = conn.execute('PRAGMA user_version').fetchone()[0]
            if existing not in (0, SCHEMA_VERSION):
                raise ContextError(f'Unsupported context schema {existing}; keep a backup before migrating')
            conn.execute('PRAGMA journal_mode=WAL')
            conn.executescript('''
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY, scope_id TEXT NOT NULL,
                    title TEXT NOT NULL, state TEXT NOT NULL DEFAULT '{}',
                    field_versions TEXT NOT NULL DEFAULT '{}', version INTEGER NOT NULL,
                    status TEXT NOT NULL, archive_reason TEXT,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    deleted_at REAL
                );
                CREATE INDEX IF NOT EXISTS tasks_scope ON tasks(scope_id, status);
                CREATE TABLE IF NOT EXISTS sessions (
                    scope_id TEXT NOT NULL, provider TEXT NOT NULL, session_id TEXT NOT NULL,
                    task_id TEXT NOT NULL REFERENCES tasks(task_id),
                    generation INTEGER NOT NULL, status TEXT NOT NULL,
                    observed_at REAL NOT NULL, source TEXT NOT NULL,
                    PRIMARY KEY(scope_id, provider, session_id)
                );
                CREATE INDEX IF NOT EXISTS sessions_task ON sessions(task_id);
                CREATE TABLE IF NOT EXISTS requests (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES tasks(task_id),
                    request_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    result_version INTEGER NOT NULL,
                    UNIQUE(task_id, request_id)
                );
                CREATE TABLE IF NOT EXISTS decisions (
                    scope_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
                    version INTEGER NOT NULL, deleted INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL, PRIMARY KEY(scope_id,key)
                );
                PRAGMA user_version=1;
                COMMIT;
            ''')

    @contextlib.contextmanager
    def _transaction(self, write=False):
        conn = self._connect()
        try:
            conn.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield conn
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            raise ContextError(f'Context database operation failed: {exc}') from exc
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _task(self, conn, task_id, allow_deleted=False):
        _identifier(task_id, 'task_id')
        row = conn.execute('SELECT * FROM tasks WHERE scope_id=? AND task_id=?',
                           (self.scope_id, task_id)).fetchone()
        if row is None:
            # Intentionally do not reveal whether another project owns this id.
            raise NotFoundError('Task not found in this project scope')
        if row['status'] == 'trashed' and not allow_deleted:
            raise DeletedTaskError('Task was deleted; open the continuing session for fresh context, or explicitly restore the old task')
        return row

    @staticmethod
    def _brief(row):
        return {key: row[key] for key in ('task_id', 'title', 'version', 'status', 'archive_reason', 'created_at', 'updated_at', 'deleted_at')}

    def _warnings(self, conn):
        count = conn.execute('SELECT COUNT(*) FROM tasks WHERE scope_id=?', (self.scope_id,)).fetchone()[0]
        return ([f'Project contains {count} tasks (soft capacity {SOFT_TASK_CAPACITY}); review/archive inactive tasks. Trash is retained until explicit recovery or separately authorized permanent removal.']
                if count >= SOFT_TASK_CAPACITY else [])

    def _new_task(self, conn, title):
        task_id = 'ctx_' + uuid.uuid4().hex
        now = time.time()
        conn.execute('INSERT INTO tasks(task_id,scope_id,title,version,status,created_at,updated_at) VALUES(?,?,?,1,\'active\',?,?)',
                     (task_id, self.scope_id, title or '', now, now))
        return self._task(conn, task_id)

    def open_session(self, provider, session_id, task_id=None, title=None):
        """Ensure an active scoped session; explicit task_id shares a live task.

        Reopening a trashed association creates empty context, even if the caller
        passes that old task id. A different unassociated trashed id is rejected.
        """
        provider = _identifier(provider, 'provider', 64)
        session_id = _identifier(session_id, 'session_id')
        if title is not None:
            _identifier(title, 'title', 256)
        with self._transaction(True) as conn:
            previous = conn.execute('SELECT * FROM sessions WHERE scope_id=? AND provider=? AND session_id=?',
                                    (self.scope_id, provider, session_id)).fetchone()
            previous_task = (conn.execute('SELECT * FROM tasks WHERE scope_id=? AND task_id=?',
                                          (self.scope_id, previous['task_id'])).fetchone()
                             if previous else None)
            reset = bool(previous and (previous_task is None or previous_task['status'] == 'trashed'))
            target = task_id or (previous['task_id'] if previous else None)
            if reset and target == previous['task_id']:
                target = None
            created = target is None
            row = self._new_task(conn, title) if created else self._task(conn, target)
            target = row['task_id']
            switching = previous is not None and previous['task_id'] != target
            generation = previous['generation'] + int(switching) if previous else 1
            now = time.time()
            conn.execute('''INSERT INTO sessions VALUES(?,?,?,?,?, 'active',?, 'session_open')
                ON CONFLICT(scope_id,provider,session_id) DO UPDATE SET
                task_id=excluded.task_id,generation=excluded.generation,status='active',
                observed_at=excluded.observed_at,source=excluded.source''',
                         (self.scope_id, provider, session_id, target, generation, now))
            if row['status'] == 'archived':
                conn.execute("UPDATE tasks SET status='active',archive_reason=NULL,version=version+1,updated_at=? WHERE task_id=?", (now, target))
            row = self._task(conn, target)
            result = dict(self._brief(row), state=json.loads(row['state']), reset=reset,
                          created=created, scope_id=self.scope_id,
                          session={'provider': provider, 'session_id': session_id,
                                   'generation': generation, 'status': 'active',
                                   'task_id': target}, warnings=self._warnings(conn))
            return result

    def read(self, task_id, since=None):
        if since is not None:
            _version(since)
        with self._transaction() as conn:
            row = self._task(conn, task_id)
            state = json.loads(row['state'])
            result = {key: row[key] for key in ('task_id', 'title', 'version', 'status')}
            if since is None:
                result['state'] = state
                result['mode'] = 'full'
            else:
                if since > row['version']:
                    raise ConflictError(since, row['version'])
                changed = {key for key, version in json.loads(row['field_versions']).items() if version > since}
                result.update(mode='delta', since=since,
                              changes={k: state[k] for k in sorted(changed) if k in state},
                              removed=sorted(changed - state.keys()))
            return result

    def patch(self, task_id, expected_version, patch_dict, request_id=None):
        return self._mutate(task_id, expected_version, patch_dict, request_id, replace=False)

    def checkpoint(self, task_id, expected_version, state, request_id=None):
        return self._mutate(task_id, expected_version, state, request_id, replace=True)

    def _mutate(self, task_id, expected_version, payload, request_id, replace):
        _version(expected_version)
        payload = _state(payload, allow_removed=not replace)
        if request_id is not None:
            _identifier(request_id, 'request_id', 128)
        fingerprint = hashlib.sha256(_json([replace, expected_version, payload]).encode()).hexdigest()
        with self._transaction(True) as conn:
            row = self._task(conn, task_id)
            if request_id is not None:
                prior = conn.execute('SELECT * FROM requests WHERE task_id=? AND request_id=?', (task_id, request_id)).fetchone()
                if prior:
                    if prior['fingerprint'] != fingerprint:
                        raise IdempotencyError('request_id was already used with different content or version')
                    return {'task_id': task_id, 'version': prior['result_version'],
                            'current_version': row['version'], 'replayed': True}
            if row['version'] != expected_version:
                raise ConflictError(expected_version, row['version'])
            old = json.loads(row['state'])
            new = payload if replace else dict(old, **payload)
            new = {key: value for key, value in new.items() if value is not None}
            _state(new)
            version = row['version'] + 1
            fields = json.loads(row['field_versions'])
            for key in old.keys() | new.keys():
                if key not in old or key not in new or old[key] != new[key]:
                    fields[key] = version
            conn.execute('UPDATE tasks SET state=?,field_versions=?,version=?,updated_at=? WHERE task_id=?',
                         (_json(new), _json(fields), version, time.time(), task_id))
            if request_id is not None:
                conn.execute('INSERT INTO requests(task_id,request_id,fingerprint,result_version) VALUES(?,?,?,?)',
                             (task_id, request_id, fingerprint, version))
                conn.execute('DELETE FROM requests WHERE task_id=? AND seq NOT IN (SELECT seq FROM requests WHERE task_id=? ORDER BY seq DESC LIMIT ?)',
                             (task_id, task_id, MAX_REQUESTS_PER_TASK))
            return {'task_id': task_id, 'version': version, 'current_version': version,
                    'replayed': False, 'state_bytes': len(_json(new).encode())}

    def list_tasks(self, include_archived=False):
        """Normal listing excludes archive and trash; explicit listing has metadata only."""
        with self._transaction() as conn:
            sql = 'SELECT * FROM tasks WHERE scope_id=?' + ('' if include_archived else " AND status='active'") + ' ORDER BY updated_at DESC,task_id'
            result = []
            for row in conn.execute(sql, (self.scope_id,)):
                item = self._brief(row)
                item['session_count'] = conn.execute('SELECT COUNT(*) FROM sessions WHERE task_id=?', (row['task_id'],)).fetchone()[0]
                result.append(item)
            return result

    def archive(self, task_id):
        with self._transaction(True) as conn:
            row = self._task(conn, task_id)
            if row['status'] != 'archived' or row['archive_reason'] != 'manual':
                conn.execute("UPDATE tasks SET status='archived',archive_reason='manual',version=version+1,updated_at=? WHERE task_id=?", (time.time(), task_id))
            return self._brief(self._task(conn, task_id))

    def delete(self, task_id):
        with self._transaction(True) as conn:
            row = self._task(conn, task_id, True)
            if row['status'] != 'trashed':
                now = time.time()
                conn.execute("UPDATE tasks SET status='trashed',archive_reason='manual_delete',deleted_at=?,version=version+1,updated_at=? WHERE task_id=?", (now, now, task_id))
            return dict(self._brief(self._task(conn, task_id, True)), recoverable=True)

    def restore(self, task_id):
        with self._transaction(True) as conn:
            row = self._task(conn, task_id, True)
            if row['status'] != 'active':
                conn.execute("UPDATE tasks SET status='active',archive_reason=NULL,deleted_at=NULL,version=version+1,updated_at=? WHERE task_id=?", (time.time(), task_id))
            # Session associations that have already moved to fresh tasks stay there.
            return self._brief(self._task(conn, task_id))

    def session_state(self, provider, session_id, status, observed_at=None, source='manual',
                      *, expected_task_id=None, expected_generation=None):
        """Record a supplied lifecycle observation, not a claim of host verification.

        expected_task_id is required to fence delayed callbacks after deletion.
        observed_at is compared with the last observation; older/equal conflicting
        observations are ignored. No path/transcript inspection is performed.
        """
        _identifier(provider, 'provider', 64)
        _identifier(session_id, 'session_id')
        _identifier(source, 'source', 64)
        if status not in SESSION_STATUSES:
            raise ContextError('Session status must be active, unknown, archived, or deleted')
        if expected_task_id is None:
            raise StaleAssociationError('expected_task_id is required for lifecycle observations')
        _identifier(expected_task_id, 'expected_task_id')
        if expected_generation is not None:
            _version(expected_generation)
        observed = _timestamp(observed_at)
        with self._transaction(True) as conn:
            row = conn.execute('SELECT * FROM sessions WHERE scope_id=? AND provider=? AND session_id=?',
                               (self.scope_id, provider, session_id)).fetchone()
            if row is None:
                raise NotFoundError('Session not found in this project scope; open it before recording status')
            if row['task_id'] != expected_task_id or (expected_generation is not None and row['generation'] != expected_generation):
                raise StaleAssociationError('Session association changed; stale lifecycle observation rejected')
            task = self._task(conn, row['task_id'], True)
            if task['status'] == 'trashed':
                raise DeletedTaskError('Deleted task cannot accept lifecycle observations; continue the session to create fresh context')
            ignored = observed < row['observed_at'] or (observed == row['observed_at'] and status != row['status'])
            if not ignored:
                conn.execute('UPDATE sessions SET status=?,observed_at=?,source=? WHERE scope_id=? AND provider=? AND session_id=?',
                             (status, observed, source, self.scope_id, provider, session_id))
            current = self._task(conn, row['task_id'])
            return dict(self._brief(current), provider=provider, session_id=session_id,
                        generation=row['generation'], ignored=ignored,
                        session_status=row['status'] if ignored else status,
                        source=row['source'] if ignored else source)

    def stats(self):
        with self._transaction() as conn:
            counts = {s: 0 for s in ('active', 'archived', 'trashed')}
            counts.update({r[0]: r[1] for r in conn.execute('SELECT status,COUNT(*) FROM tasks WHERE scope_id=? GROUP BY status', (self.scope_id,))})
            sessions = {r[0]: r[1] for r in conn.execute('SELECT status,COUNT(*) FROM sessions WHERE scope_id=? GROUP BY status', (self.scope_id,))}
            state_bytes = conn.execute('SELECT COALESCE(SUM(length(CAST(state AS BLOB))),0) FROM tasks WHERE scope_id=?', (self.scope_id,)).fetchone()[0]
            requests = conn.execute('SELECT COUNT(*) FROM requests JOIN tasks USING(task_id) WHERE scope_id=?', (self.scope_id,)).fetchone()[0]
            return {'scope_id': self.scope_id, 'scope_identity': self.scope_identity,
                    'host': hosts.CURRENT, 'database': str(self.path), 'tasks': counts, 'total_tasks': sum(counts.values()),
                    'sessions': sessions, 'state_bytes': state_bytes, 'request_receipts': requests,
                    'soft_task_capacity': SOFT_TASK_CAPACITY, 'warnings': self._warnings(conn),
                    'retention': 'Current state and field versions only; at most 128 idempotency receipts per task. Trash and decisions are retained. Unknown sessions are never inferred closed.'}

    def gc(self, apply=False):
        """Compatibility diagnostic: automatic lifecycle cleanup is disabled.

        No task or receipt is removed, including when apply=True. Use explicit
        archive/delete/restore for task maintenance; unknown sessions are retained.
        """
        return {'applied': bool(apply), 'archive_candidates': [], 'archived': [],
                'permanently_deleted': 0, 'warnings': self.stats()['warnings'],
                'note': 'Automatic lifecycle cleanup is disabled. Use manual archive or recoverable delete; this diagnostic does not modify records.'}

    def _decision_scope(self, scope):
        if scope not in ('project', 'global'):
            raise ContextError('Decision scope must be project or global')
        return self.scope_id if scope == 'project' else 'global'

    def decision_put(self, key, value, scope='project', expected_version=0):
        _identifier(key, 'decision key', 128)
        _identifier(value, 'decision value', MAX_FIELD_CHARS)
        _version(expected_version)
        scope_id = self._decision_scope(scope)
        with self._transaction(True) as conn:
            old = conn.execute('SELECT * FROM decisions WHERE scope_id=? AND key=?', (scope_id, key)).fetchone()
            version = old['version'] if old else 0
            if version != expected_version:
                raise ConflictError(expected_version, version)
            version += 1
            conn.execute('''INSERT INTO decisions VALUES(?,?,?,?,0,?) ON CONFLICT(scope_id,key)
                DO UPDATE SET value=excluded.value,version=excluded.version,deleted=0,updated_at=excluded.updated_at''',
                         (scope_id, key, value, version, time.time()))
            return {'key': key, 'value': value, 'version': version, 'scope': scope, 'deleted': False}

    def decision_list(self, scope='project', include_deleted=False):
        scope_id = self._decision_scope(scope)
        with self._transaction() as conn:
            rows = conn.execute('SELECT * FROM decisions WHERE scope_id=?' + ('' if include_deleted else ' AND deleted=0') + ' ORDER BY key', (scope_id,)).fetchall()
            # Deleted content remains recoverable but is not returned implicitly.
            return [dict(key=r['key'], version=r['version'], scope=scope, deleted=bool(r['deleted']),
                         **({'value': r['value']} if not r['deleted'] else {})) for r in rows]

    def decision_delete(self, key, scope='project', expected_version=None):
        _identifier(key, 'decision key', 128)
        _version(expected_version)
        scope_id = self._decision_scope(scope)
        with self._transaction(True) as conn:
            row = conn.execute('SELECT * FROM decisions WHERE scope_id=? AND key=?', (scope_id, key)).fetchone()
            if row is None:
                raise NotFoundError('Decision not found in this scope')
            if row['version'] != expected_version:
                raise ConflictError(expected_version, row['version'])
            version = row['version'] + (not row['deleted'])
            conn.execute('UPDATE decisions SET deleted=1,version=?,updated_at=? WHERE scope_id=? AND key=?',
                         (version, time.time(), scope_id, key))
            return {'key': key, 'version': version, 'scope': scope, 'deleted': True, 'recoverable': True}

    def decision_restore(self, key, scope='project', expected_version=None):
        _identifier(key, 'decision key', 128)
        _version(expected_version)
        scope_id = self._decision_scope(scope)
        with self._transaction(True) as conn:
            row = conn.execute('SELECT * FROM decisions WHERE scope_id=? AND key=?', (scope_id, key)).fetchone()
            if row is None:
                raise NotFoundError('Decision not found in this scope')
            if row['version'] != expected_version:
                raise ConflictError(expected_version, row['version'])
            version = row['version'] + bool(row['deleted'])
            conn.execute('UPDATE decisions SET deleted=0,version=?,updated_at=? WHERE scope_id=? AND key=?',
                         (version, time.time(), scope_id, key))
            return {'key': key, 'value': row['value'], 'version': version, 'scope': scope, 'deleted': False}
