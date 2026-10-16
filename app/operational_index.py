"""Small transactional control projection; no candles or historical rewrites.

Risk semantics match the original gate, including legacy expiry-time fallback.
Indexes bound each poll to the entry/pause windows and a short preceding streak.
"""


class OperationalIndex:
    def __init__(self, db):
        self.db = db
        db.executescript('''
        CREATE TABLE IF NOT EXISTS signal_controls(
            signal_id TEXT PRIMARY KEY, sequence INTEGER, created REAL, entry_at REAL, state TEXT,
            emitted INTEGER, telegram_status TEXT, settled_at REAL,
            result TEXT, settlement_pending INTEGER, score REAL);
        ''')
        columns={r[1] for r in db.execute('PRAGMA table_info(signal_controls)')}
        if 'score' not in columns:
            db.execute('ALTER TABLE signal_controls ADD COLUMN score REAL')
            db.execute("UPDATE signal_controls SET score=(SELECT json_extract(payload,'$.score') FROM signals WHERE id=signal_id)")
        db.executescript('''
        DROP TRIGGER IF EXISTS controls_insert;
        DROP TRIGGER IF EXISTS controls_update;
        CREATE INDEX IF NOT EXISTS idx_controls_created ON signal_controls(created DESC,score DESC,sequence ASC);
        CREATE INDEX IF NOT EXISTS idx_controls_active ON signal_controls(state,created DESC,score DESC,sequence ASC);
        CREATE INDEX IF NOT EXISTS idx_controls_entry ON signal_controls(entry_at);
        CREATE INDEX IF NOT EXISTS idx_controls_settled ON signal_controls(settled_at,created,sequence)
          WHERE state='encerrado' AND settlement_pending=0 AND result IN ('WIN','LOSS','EMPATE');
        CREATE TRIGGER IF NOT EXISTS controls_insert AFTER INSERT ON signals BEGIN
          INSERT OR REPLACE INTO signal_controls VALUES(NEW.id,NEW.rowid,NEW.created,
            json_extract(NEW.payload,'$.entry_at'),json_extract(NEW.payload,'$.state'),
            json_extract(NEW.payload,'$.emitted_at') IS NOT NULL,json_extract(NEW.payload,'$.telegram.status'),
            COALESCE(json_extract(NEW.payload,'$.settled_at'),json_extract(NEW.payload,'$.expiry_at')),
            json_extract(NEW.payload,'$.result'),COALESCE(json_extract(NEW.payload,'$.settlement_pending'),0),json_extract(NEW.payload,'$.score'));
        END;
        CREATE TRIGGER IF NOT EXISTS controls_update AFTER UPDATE OF payload ON signals BEGIN
          INSERT OR REPLACE INTO signal_controls VALUES(NEW.id,NEW.rowid,NEW.created,
            json_extract(NEW.payload,'$.entry_at'),json_extract(NEW.payload,'$.state'),
            json_extract(NEW.payload,'$.emitted_at') IS NOT NULL,json_extract(NEW.payload,'$.telegram.status'),
            COALESCE(json_extract(NEW.payload,'$.settled_at'),json_extract(NEW.payload,'$.expiry_at')),
            json_extract(NEW.payload,'$.result'),COALESCE(json_extract(NEW.payload,'$.settlement_pending'),0),json_extract(NEW.payload,'$.score'));
        END;
        INSERT OR IGNORE INTO signal_controls
          SELECT id,rowid,created,json_extract(payload,'$.entry_at'),json_extract(payload,'$.state'),
            json_extract(payload,'$.emitted_at') IS NOT NULL,json_extract(payload,'$.telegram.status'),
            COALESCE(json_extract(payload,'$.settled_at'),json_extract(payload,'$.expiry_at')),
            json_extract(payload,'$.result'),COALESCE(json_extract(payload,'$.settlement_pending'),0),json_extract(payload,'$.score') FROM signals;
        ''')
        db.commit()

    def limits(self, now, entry, settings):
        capacity = self.db.execute("SELECT count(*),COALESCE(sum(state IN ('aguardando entrada','em andamento')),0) "
            "FROM signal_controls INDEXED BY idx_controls_entry WHERE entry_at>? AND entry_at<? AND "
            "(state!='cancelado' OR emitted=1 OR telegram_status IN ('enviado','incerto','enviando'))",
            (entry-3600,entry+3600)).fetchone()
        # A pause may have started before a subsequent WIN. Retain all results
        # in the pause window plus enough preceding losses to establish it.
        cutoff = now-settings['pause_minutes']*60
        condition = "state='encerrado' AND settlement_pending=0 AND result IN ('WIN','LOSS','EMPATE')"
        prior = list(self.db.execute('SELECT result,settled_at FROM signal_controls INDEXED BY idx_controls_settled WHERE '+condition+
            ' AND settled_at<=? ORDER BY settled_at DESC,created DESC,sequence DESC LIMIT ?',
            (cutoff,settings['loss_pause_after']-1)))
        recent = self.db.execute('SELECT result,settled_at FROM signal_controls INDEXED BY idx_controls_settled WHERE '+condition+
            ' AND settled_at>? AND settled_at<=? ORDER BY settled_at,created,sequence', (cutoff,now))
        streak=pause_until=0
        for row in [*reversed(prior),*recent]:
            if row['result']=='LOSS':
                streak+=1
                if streak>=settings['loss_pause_after']:
                    pause_until=max(pause_until,row['settled_at']+settings['pause_minutes']*60)
            else:streak=0
        return {**settings,'used':capacity[0],'remaining':max(0,settings['hourly_limit']-capacity[0]),
                'active':capacity[1],'pause_until':pause_until if pause_until>now else 0,
                'loss_streak':settings['loss_pause_after'] if pause_until>now else 0}
