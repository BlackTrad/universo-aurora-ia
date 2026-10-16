"""Derived, bounded recovery queue; original decisions and feedback are immutable."""
import json
import math

SCHEMA_VERSION = '1.26.6-1'
# Audited releases share the original-source closed-M1 outcome contract.
# Recovery compatibility never merges strategy/pipeline learning contexts.
COMPATIBLE_RELEASES = ('3.6.0','3.7.0','3.8.0','3.8.1','3.8.2','3.9.0')


def compatible_versions(current):
    """Recover only explicitly compatible current and preceding releases."""
    parts = current.split('.')
    if len(parts) == 3 and parts[:2] == ['1', '26'] and parts[2].isdigit() and 5 <= int(parts[2]) <= 999:
        return tuple(f'1.26.{patch}' for patch in range(5, int(parts[2]) + 1))
    releases=(COMPATIBLE_RELEASES[:COMPATIBLE_RELEASES.index(current)+1]
              if current in COMPATIBLE_RELEASES else (current,))
    return tuple(dict.fromkeys((*[f'1.26.{patch}' for patch in range(5, 17)], *releases)))


class FeedbackQueue:
    def __init__(self, db, versions):
        self.db, self.versions = db, tuple(versions)
        db.executescript('''
        CREATE TABLE IF NOT EXISTS feedback_jobs(
            signal_id TEXT PRIMARY KEY, eligible_at REAL NOT NULL,
            ready_at REAL NOT NULL, retry_at REAL NOT NULL DEFAULT 0,
            created_at REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS feedback_jobs_ready ON feedback_jobs(ready_at,created_at,signal_id);
        CREATE TABLE IF NOT EXISTS feedback_queue_meta(id INTEGER PRIMARY KEY CHECK(id=1),version TEXT NOT NULL);
        ''')
        # Compatibility is part of the recovery policy even if the SQL schema
        # is unchanged. This also reconciles the original 1.26.6 metadata once.
        policy = SCHEMA_VERSION + ':' + ','.join(sorted(set(self.versions)))
        previous = db.execute('SELECT version FROM feedback_queue_meta WHERE id=1').fetchone()
        if previous is None or previous[0] != policy:
            # Only once per schema/policy, never a full-payload scan per tick.
            placeholders = ','.join('?' for _ in self.versions)
            rows = db.execute('SELECT s.payload FROM signals s LEFT JOIN learning_feedback f ON f.signal_id=s.id '
                              'WHERE f.signal_id IS NULL AND json_extract(s.payload,\'$.settled_by_version\') '
                              f'IN ({placeholders})', self.versions).fetchall()
            with db:
                for row in rows:
                    self.sync(json.loads(row[0]))
                db.execute('INSERT OR REPLACE INTO feedback_queue_meta VALUES(1,?)', (policy,))

    def sync(self, signal):
        sid = signal['id']
        expiry, settled = signal.get('expiry_at'), signal.get('settled_at', 0)
        numeric = all(type(value) in (int, float) and math.isfinite(value) for value in (expiry, settled))
        eligible = (numeric and expiry > 0 and settled >= 0
                    and signal.get('settled_by_version') in self.versions
                    and signal.get('state') == 'encerrado' and signal.get('settlement_pending') is False
                    and signal.get('result') in ('WIN', 'LOSS', 'EMPATE', 'Resultado indisponível')
                    and not self.db.execute('SELECT 1 FROM learning_feedback WHERE signal_id=?', (sid,)).fetchone())
        if not eligible:
            self.complete(sid)
            return
        known_at = max(expiry + 60, settled)
        # Existing retry deadlines survive later delivery patches and restarts.
        self.db.execute('''INSERT INTO feedback_jobs VALUES(?,?,?,0,?)
            ON CONFLICT(signal_id) DO UPDATE SET eligible_at=excluded.eligible_at,
            ready_at=MAX(excluded.eligible_at,feedback_jobs.retry_at)''',
                        (sid, known_at, known_at, signal.get('created_at', expiry)))

    def pending(self, now, limit):
        rows = self.db.execute('''SELECT s.payload FROM feedback_jobs q JOIN signals s ON s.id=q.signal_id
            WHERE q.ready_at<=? ORDER BY q.ready_at,q.created_at,q.signal_id LIMIT ?''', (now, limit))
        return [json.loads(row[0]) for row in rows]

    def defer(self, sid, retry_at):
        self.db.execute('UPDATE feedback_jobs SET retry_at=?,ready_at=MAX(eligible_at,?) WHERE signal_id=?',
                        (retry_at, retry_at, sid))

    def complete(self, sid):
        self.db.execute('DELETE FROM feedback_jobs WHERE signal_id=?', (sid,))
