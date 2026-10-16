import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from .logging_setup import event
from .learning import RULES_VERSION
from .version import VERSION

class Store:
    def __init__(self,path):
        if path != ':memory:': Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.db = sqlite3.connect(path,check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS signals(id TEXT PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL, created REAL, payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created DESC);
        CREATE INDEX IF NOT EXISTS idx_signals_entry ON signals(json_extract(payload,'$.entry_at'));
        CREATE INDEX IF NOT EXISTS idx_signals_state ON signals(json_extract(payload,'$.state'));
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, signal_id TEXT, at REAL, kind TEXT, detail TEXT);
        CREATE INDEX IF NOT EXISTS idx_events_signal ON events(signal_id,id);
        CREATE TABLE IF NOT EXISTS analyses(id TEXT PRIMARY KEY, created REAL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS lease(id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT, until REAL);
        CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT);
        CREATE TABLE IF NOT EXISTS validation_decisions(id TEXT PRIMARY KEY, created REAL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS validation_events(id INTEGER PRIMARY KEY, decision_id TEXT, at REAL, payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_validation_events ON validation_events(decision_id,id);
        CREATE TABLE IF NOT EXISTS learning_feedback(signal_id TEXT PRIMARY KEY, known_at REAL NOT NULL, payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_feedback_known ON learning_feedback(known_at DESC);
        CREATE TRIGGER IF NOT EXISTS feedback_no_update BEFORE UPDATE ON learning_feedback BEGIN SELECT RAISE(ABORT,'immutable learning feedback'); END;
        CREATE TRIGGER IF NOT EXISTS feedback_no_delete BEFORE DELETE ON learning_feedback BEGIN SELECT RAISE(ABORT,'immutable learning feedback'); END;
        CREATE TABLE IF NOT EXISTS signal_origins(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS movement_claims(movement_id TEXT PRIMARY KEY, signal_id TEXT NOT NULL, created REAL NOT NULL);
        CREATE TRIGGER IF NOT EXISTS movement_claims_no_update BEFORE UPDATE ON movement_claims BEGIN SELECT RAISE(ABORT,'immutable movement claim'); END;
        CREATE TRIGGER IF NOT EXISTS movement_claims_no_delete BEFORE DELETE ON movement_claims BEGIN SELECT RAISE(ABORT,'immutable movement claim'); END;
        INSERT OR IGNORE INTO movement_claims SELECT json_extract(payload,'$.movement_id'),id,created
          FROM signals WHERE json_extract(payload,'$.movement_id') IS NOT NULL ORDER BY created,rowid;
        CREATE TABLE IF NOT EXISTS manual_confirmations(id INTEGER PRIMARY KEY, signal_id TEXT, at REAL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS history_archives(signal_id TEXT PRIMARY KEY NOT NULL, batch_id TEXT NOT NULL, at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS history_clear_batches(id TEXT PRIMARY KEY, at REAL NOT NULL, scope TEXT NOT NULL, period TEXT, count INTEGER NOT NULL);
        CREATE TRIGGER IF NOT EXISTS history_batches_no_update BEFORE UPDATE ON history_clear_batches BEGIN SELECT RAISE(ABORT,'immutable history clear batch'); END;
        CREATE TRIGGER IF NOT EXISTS history_batches_no_delete BEFORE DELETE ON history_clear_batches BEGIN SELECT RAISE(ABORT,'immutable history clear batch'); END;
        CREATE TRIGGER IF NOT EXISTS history_archives_no_update BEFORE UPDATE ON history_archives BEGIN SELECT RAISE(ABORT,'immutable history archive'); END;
        CREATE TRIGGER IF NOT EXISTS history_archives_no_delete BEFORE DELETE ON history_archives BEGIN SELECT RAISE(ABORT,'immutable history archive'); END;
        CREATE INDEX IF NOT EXISTS idx_manual_signal ON manual_confirmations(signal_id,id);
        CREATE TRIGGER IF NOT EXISTS decisions_no_update BEFORE UPDATE ON validation_decisions BEGIN SELECT RAISE(ABORT,'immutable decision'); END;
        CREATE TRIGGER IF NOT EXISTS decisions_no_delete BEFORE DELETE ON validation_decisions BEGIN SELECT RAISE(ABORT,'immutable decision'); END;
        CREATE TRIGGER IF NOT EXISTS origins_no_update BEFORE UPDATE ON signal_origins BEGIN SELECT RAISE(ABORT,'immutable origin'); END;
        CREATE TRIGGER IF NOT EXISTS confirmations_no_update BEFORE UPDATE ON manual_confirmations BEGIN SELECT RAISE(ABORT,'append-only confirmation'); END;
        CREATE TRIGGER IF NOT EXISTS confirmations_no_delete BEFORE DELETE ON manual_confirmations BEGIN SELECT RAISE(ABORT,'append-only confirmation'); END;
        ''')
        self.db.commit()
        from .validation_index import ValidationIndex
        self.metric_index=ValidationIndex(self)
        from .operational_index import OperationalIndex
        self.operational_index=OperationalIndex(self.db)
        from .feedback_queue import FeedbackQueue, compatible_versions
        # These releases share the same settlement/feedback contract. Never drop
        # the preceding release when adding the current distribution version.
        self.feedback_queue=FeedbackQueue(self.db,versions=compatible_versions(VERSION))
        self.closed=False

    def close(self):
        # Cancelling an awaiting coroutine does not cancel its SQLite worker.
        with self.lock:
            if not self.closed:
                self.db.close()
                self.closed=True

    def record_learning_feedback(self,feedback):
        with self.lock,self.db:
            inserted=bool(self.db.execute('INSERT OR IGNORE INTO learning_feedback VALUES(?,?,?)',
                (feedback['signal_id'],feedback['known_at'],json.dumps(feedback))).rowcount)
            self.feedback_queue.complete(feedback['signal_id'])
            return inserted

    def latest_learning_feedback(self,now):
        with self.lock:
            row=self.db.execute('SELECT payload FROM learning_feedback WHERE known_at<=? ORDER BY known_at DESC,rowid DESC LIMIT 1',(now,)).fetchone()
            return json.loads(row[0]) if row else None

    def pending_learning_feedback(self,now,limit=1):
        with self.lock:
            return self.feedback_queue.pending(now,limit)

    def defer_learning_feedback(self,sid,retry_at):
        with self.lock,self.db:
            self.feedback_queue.defer(sid,retry_at)

    def acquire(self,owner,now,ttl=180):
        with self.lock,self.db:
            self.db.execute('INSERT OR IGNORE INTO lease VALUES(1,?,?)',(owner,now+ttl))
            row = self.db.execute('SELECT owner,until FROM lease WHERE id=1').fetchone()
            if row['owner'] != owner and row['until'] > now: return False
            self.db.execute('UPDATE lease SET owner=?,until=? WHERE id=1',(owner,now+ttl))
            return True

    def release(self,owner):
        with self.lock,self.db:
            self.db.execute('DELETE FROM lease WHERE owner=?',(owner,))

    def busy(self,now):
        with self.lock:
            return bool(self.db.execute('SELECT 1 FROM lease WHERE until>?',(now,)).fetchone())

    def add_signal(self,s,fingerprint):
        with self.lock,self.db:
            if s.get('movement_id') and self.db.execute('SELECT 1 FROM movement_claims WHERE movement_id=?',(s['movement_id'],)).fetchone():
                return False
            try:
                self.db.execute('INSERT INTO signals VALUES(?,?,?,?)',(s['id'],fingerprint,s['created_at'],json.dumps(s)))
            except sqlite3.IntegrityError:
                event('signal.duplicate',signal_id=s['id'],asset=s.get('asset'))
                return False
            self.db.execute('INSERT INTO signal_origins VALUES(?,?)',(s['id'],json.dumps(s)))
            if s.get('movement_id'):
                self.db.execute('INSERT INTO movement_claims VALUES(?,?,?)',(s['movement_id'],s['id'],s['created_at']))
            self.metric_index.sync(s)
            self.feedback_queue.sync(s)
            self._event(s['id'],'aguardando entrada',s['created_at'],'Sinal criado pelo backend')
            event('signal.created',signal_id=s['id'],asset=s.get('asset'),direction=s.get('direction'),provider=s.get('source_key'),
                  strategy=s.get('strategy'),resolved=s.get('strategy_resolved'),entry_at=s.get('entry_at'),expiry_at=s.get('expiry_at'))
            return True

    def risk_settings(self):
        saved=self.settings({})
        specs={'hourly_limit':(10,1,60),'simultaneous_limit':(2,1,2),
               'loss_pause_after':(2,1,10),'pause_minutes':(15,1,120),'minimum_score':(70,70,100)}
        return {k:v if type(v:=saved.get(k)) is int and lo<=v<=hi else default
                for k,(default,lo,hi) in specs.items()}

    def add_apex_signal(self, s, fingerprint):
        """Persist a signal only if risk limits and the two-signal concurrency cap permit it.

        The check and insert share one lock so simultaneous analyses cannot exceed
        the hourly cap. Entry times, rather than scan cycles, define operations.
        """
        with self.lock, self.db:
            now = s['created_at']
            gate = self.apex_limits(now, s['entry_at'])
            if s.get('movement_id') and self.db.execute('SELECT 1 FROM movement_claims WHERE movement_id=?',(s['movement_id'],)).fetchone():
                return False, 'Movimento original já emitido; sinal duplicado bloqueado.'
            if self.db.execute('SELECT 1 FROM signals WHERE fingerprint=?', (fingerprint,)).fetchone():
                return False, 'Setup já emitido; sinal duplicado bloqueado.'
            duplicate=self.db.execute("SELECT 1 FROM signals WHERE "
                "json_extract(payload,'$.asset')=? AND json_extract(payload,'$.source_key')=? "
                "AND COALESCE(json_extract(payload,'$.source_identity'),'')=? "
                "AND json_extract(payload,'$.state') IN ('aguardando entrada','em andamento') "
                "AND json_extract(payload,'$.expiry_at')>? LIMIT 1",
                (s.get('asset'),s.get('source_key'),s.get('source_identity',''),now)).fetchone()
            if duplicate:
                return False,'Já existe sinal ativo nesse ativo e fonte; operação duplicada bloqueada.'
            if gate['pause_until'] > now:
                return False, f"Após {gate['loss_pause_after']} losses consecutivos, faça uma pausa de {gate['pause_minutes']} minutos."
            if gate['active'] >= gate['simultaneous_limit']:
                return False, f"Limite de {gate['simultaneous_limit']} sinais simultâneos atingido."
            if gate['remaining'] == 0:
                return False, f"Limite de {gate['hourly_limit']} sinais na janela de 60 minutos."
            try:
                self.db.execute('INSERT INTO signals VALUES(?,?,?,?)',
                                (s['id'], fingerprint, now, json.dumps(s)))
            except sqlite3.IntegrityError:
                return False, 'Setup já emitido; sinal duplicado bloqueado.'
            self.db.execute('INSERT INTO signal_origins VALUES(?,?)',(s['id'],json.dumps(s)))
            if s.get('movement_id'):
                self.db.execute('INSERT INTO movement_claims VALUES(?,?,?)',(s['movement_id'],s['id'],now))
            self.metric_index.sync(s)
            self.feedback_queue.sync(s)
            self._event(s['id'], 'aguardando entrada', now, 'Sinal AURORA criado')
            event('signal.created', signal_id=s['id'], asset=s.get('asset'), provider=s.get('source_key'),
                  strategy=s.get('strategy_resolved'), direction=s.get('direction'), score=s.get('score'),
                  entry_at=s.get('entry_at'), expiry_at=s.get('expiry_at'),
                  rules_version=s.get('evidence',{}).get('rules_version'),
                  pipeline_version=s.get('evidence',{}).get('pipeline_version'), quality=s.get('quality'))
            return True, ''

    def apex_limits(self, now, proposed_entry=None):
        """Indexed rolling-hour capacity and pause; original signals stay intact."""
        with self.lock:
            return self.operational_index.limits(now,proposed_entry if proposed_entry is not None else now,
                                                  self.risk_settings())

    def result_totals(self,include_archived=True,*,verified=False,now=None):
        """All saved, resolved indicative signals; ties excluded from win rate."""
        if verified:
            now=time.time() if now is None else now
            with self.lock:
                visible=" AND signal_id NOT IN (SELECT signal_id FROM history_archives)" if not include_archived else ''
                rows=self.db.execute("SELECT outcome,price_verified,count(*) AS n FROM metric_projection "
                    "WHERE scope='bot' AND created<=? AND outcome IN ('WIN','LOSS','EMPATE') "
                    "AND (resolved_at IS NULL OR resolved_at<=?)"+visible+" GROUP BY outcome,price_verified",(now,now)).fetchall()
            counts={r['outcome']:r['n'] for r in rows if r['price_verified']}
            wins,losses=counts.get('WIN',0),counts.get('LOSS',0)
            return {'wins':wins,'losses':losses,'ties':counts.get('EMPATE',0),
                'win_rate_pct':round(100*wins/(wins+losses),2) if wins+losses else None,
                'unverified':sum(r['n'] for r in rows if not r['price_verified']),
                'basis':'exact_original_source_closed_M1','scope':('Histórico total' if include_archived else 'Histórico visível')+' · indicativo com prova de preço'}
        with self.lock:
            visible=" AND id NOT IN (SELECT signal_id FROM history_archives)" if not include_archived else ''
            rows = self.db.execute("SELECT json_extract(payload,'$.result') AS result, COUNT(*) AS n FROM signals WHERE json_extract(payload,'$.state')='encerrado' AND COALESCE(json_extract(payload,'$.settlement_pending'),0)=0"+visible+" GROUP BY result")
            counts = {r['result']: r['n'] for r in rows}
        wins, losses = counts.get('WIN',0), counts.get('LOSS',0)
        return {'wins':wins, 'losses':losses, 'ties':counts.get('EMPATE',0),
                'win_rate_pct':round(100*wins/(wins+losses),2) if wins+losses else None,
                'scope':'Histórico total · resultados indicativos' if include_archived else 'Histórico visível · indicativo'}

    @staticmethod
    def result_message_ready(s):
        if s.get('state')=='cancelado': return s.get('result')=='CANCELADO'
        return (s.get('state')=='encerrado' and not s.get('settlement_pending',False)
                and s.get('result') in ('WIN','LOSS','EMPATE','Resultado indisponível'))

    def pending_result_messages(self,now=None):
        now=time.time() if now is None else now
        with self.lock:
            rows=[json.loads(r[0]) for r in self.db.execute(
                "SELECT payload FROM signals WHERE json_extract(payload,'$.telegram_result.status')='pendente' "
                "OR (json_extract(payload,'$.telegram_result.status')='repetir' "
                "AND json_extract(payload,'$.telegram_result.retry_at')<=?) ORDER BY created",(now,))]
            return [s for s in rows if self.result_message_ready(s)]

    def claim_result_message(self, sid, now):
        with self.lock, self.db:
            row=self.db.execute('SELECT payload FROM signals WHERE id=?',(sid,)).fetchone()
            if not row: return False
            s=json.loads(row[0])
            previous=s.get('telegram_result',{})
            if not self.result_message_ready(s): return False
            if previous.get('status') not in ('pendente','repetir'): return False
            if previous.get('status')=='repetir' and previous.get('retry_at',float('inf'))>now: return False
            attempt=previous.get('attempt',0)+1
            if attempt>3: return False
            s['telegram_result']={**previous,'status':'enviando','at':now,'attempt':attempt}
            s['telegram_result'].pop('retry_at',None)
            self.db.execute('UPDATE signals SET payload=? WHERE id=?',(json.dumps(s),sid))
            self._event(sid,'telegram resultado tentativa',now,str({'attempt':attempt,'status':'enviando'}))
            return True

    def apex_metrics(self):
        from .validation_metrics import grouped
        return grouped([s for s in self.visible_signals(None) if s.get('state')!='cancelado'])

    def visible_signals(self,limit=100):
        with self.lock:
            query="SELECT payload FROM signals WHERE id NOT IN (SELECT signal_id FROM history_archives) ORDER BY created DESC, json_extract(payload,'$.score') DESC, rowid ASC"
            return [json.loads(r[0]) for r in self.db.execute(query+(' LIMIT ?' if limit is not None else ''),(limit,) if limit is not None else ())]

    def clear_history(self,scope,period,now,start=None,end=None):
        """Archive terminal rows from the UI, without altering outcomes, risk or learning."""
        conditions=['id NOT IN (SELECT signal_id FROM history_archives)'];args=[]
        if start is not None:conditions.append("json_extract(payload,'$.entry_at')>=?");args.append(start)
        if end is not None:conditions.append("json_extract(payload,'$.entry_at')<?");args.append(end)
        with self.lock,self.db:
            rows=self.db.execute('SELECT id,payload FROM signals WHERE '+' AND '.join(conditions),args).fetchall()
            eligible=[r['id'] for r in rows if (s:=json.loads(r['payload'])).get('state')=='cancelado'
                      or (s.get('state')=='encerrado' and not s.get('settlement_pending',False))]
            batch_id=uuid.uuid4().hex if eligible else None
            if eligible:
                self.db.execute('INSERT INTO history_clear_batches VALUES(?,?,?,?,?)',(batch_id,now,scope,period,len(eligible)))
                self.db.executemany('INSERT INTO history_archives VALUES(?,?,?)',[(sid,batch_id,now) for sid in eligible])
                event('history.cleared',batch_id=batch_id,scope=scope,period=period,count=len(eligible),pending_preserved=len(rows)-len(eligible))
            return {'cleared':len(eligible),'pending_preserved':len(rows)-len(eligible),'batch_id':batch_id,
                    'scope':scope,'period':period,'mode':'archive_from_visible_history',
                    'note':'Operações concluídas removidas da visualização. Auditoria, aprendizado e controles de risco preservados; sinais pendentes continuam.'}

    def all_signals(self):
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute('SELECT payload FROM signals ORDER BY created,id')]

    def record_decision(self,record):
        with self.lock,self.db:
            self.db.execute('INSERT INTO validation_decisions VALUES(?,?,?)',
                            (record['id'],record['created_at'],json.dumps(record,ensure_ascii=False,allow_nan=False)))

    def decision_event(self,did,now,event):
        with self.lock,self.db:
            self.db.execute('INSERT INTO validation_events(decision_id,at,payload) VALUES(?,?,?)',
                            (did,now,json.dumps(event,ensure_ascii=False,allow_nan=False)))

    def decisions(self,limit=100,after_rowid=0):
        with self.lock:
            rows=self.db.execute('SELECT rowid,payload FROM validation_decisions WHERE rowid>? ORDER BY rowid LIMIT ?',
                                 (after_rowid,limit)).fetchall()
            items=[]
            for row in rows:
                record=json.loads(row['payload'])
                events=[{'at':e['at'],**json.loads(e['payload'])} for e in self.db.execute(
                    'SELECT at,payload FROM validation_events WHERE decision_id=? ORDER BY id',(record['id'],))]
                items.append({'rowid':row['rowid'],'decision':record,'events':events})
            return {'items':items,'next_after':rows[-1]['rowid'] if rows else after_rowid}

    def planetary_performance(self, now, limit=5000):
        """One consistent read-only SQLite snapshot, with an explicit sampling cap."""
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError('Invalid audit limit')
        from .planetary_performance import summarize_planetary
        with self.lock:
            count = self.db.execute('SELECT count(*) FROM validation_decisions WHERE created<=?',
                                    (now,)).fetchone()[0]
            # Most recent N decisions, then chronological presentation.
            decision_rows = self.db.execute(
                'SELECT id,payload FROM validation_decisions WHERE created<=? '
                'ORDER BY created DESC, id DESC LIMIT ?', (now, limit)).fetchall()
            items = []
            for d in reversed(decision_rows):
                events = self.db.execute(
                    'SELECT at,payload FROM validation_events WHERE decision_id=? AND at<=? '
                    'ORDER BY id', (d['id'], now)).fetchall()
                items.append({'decision': json.loads(d['payload']),
                              'events': [{'at': row['at'], **json.loads(row['payload'])}
                                         for row in events]})
            ids = [d['id'] for d in decision_rows]
            signals = []
            if ids:
                for start in range(0, len(ids), 400):
                    batch = ids[start:start + 400]
                    placeholders = ','.join('?' for _ in batch)
                    records = self.db.execute(
                        f"SELECT payload FROM signals WHERE json_extract(payload,'$.decision_id') "
                        f"IN ({placeholders}) AND created<=?", (*batch, now)).fetchall()
                    signals.extend(json.loads(row['payload']) for row in records)
        return summarize_planetary(items, signals, now=now, source_count=count)

    def confirm_manual(self,sid,value,now):
        with self.lock,self.db:
            signal=self.signal(sid)
            if signal is None:raise KeyError(sid)
            previous=self.db.execute('SELECT id,payload FROM manual_confirmations WHERE signal_id=? ORDER BY id DESC LIMIT 1',(sid,)).fetchone()
            if previous:
                old=json.loads(previous['payload'])
                if all(old.get(k)==v for k,v in value.items()):
                    return {'confirmation_id':previous['id'],**old}
            record={**value,'signal_id':sid,'recorded_at':now,'supersedes':previous['id'] if previous else None,
                    'kind':'user_declared_manual','externally_verified':False,
                    'payout_origin':'user_declared' if value.get('payout_pct') is not None else None,
                    'differences_at_recording':{
                        'result':value.get('result')!=signal.get('result') if value.get('executed') else None,
                        'entry_seconds':value['entry_at']-signal['entry_at'] if value.get('entry_at') is not None else None,
                        'expiry_seconds':value['expiry_at']-signal['expiry_at'] if value.get('expiry_at') is not None else None,
                        'entry_price':value['entry_price']-signal['entry_price'] if value.get('entry_price') is not None and signal.get('entry_price') is not None else None,
                        'expiry_price':value['expiry_price']-signal['expiry_price'] if value.get('expiry_price') is not None and signal.get('expiry_price') is not None else None}}
            cursor=self.db.execute('INSERT INTO manual_confirmations(signal_id,at,payload) VALUES(?,?,?)',(sid,now,json.dumps(record,ensure_ascii=False,allow_nan=False)))
            self.metric_index.sync(signal)
            return {'confirmation_id':cursor.lastrowid,**record}

    def manual_confirmations(self,sid=None):
        with self.lock:
            query='SELECT id,payload FROM manual_confirmations'+(' WHERE signal_id=?' if sid else '')+' ORDER BY id'
            return [{'confirmation_id':r['id'],**json.loads(r['payload'])} for r in self.db.execute(query,(sid,) if sid else ())]

    def validation_report(self,now):
        from .validation_metrics import grouped, result_of
        from .validation_index import manual_view
        from .outcome_evidence import comparable_outcome
        signals=[s for s in self.all_signals() if s.get('created_at',0)<=now]
        signals=[{**s,'settlement_pending':True} if s.get('settled_at',0)>now else s for s in signals]
        latest={c['signal_id']:c for c in self.manual_confirmations() if c['recorded_at']<=now}
        manual=[]
        for s in signals:
            c=latest.get(s['id'])
            if c and c['executed']:
                manual.append(manual_view(s,c))
        generated=len(signals);sent=sum(s.get('telegram',{}).get('status')=='enviado' for s in signals)
        verified_view=[];unproven=0
        for s in signals:
            if result_of(s) in ('WIN','LOSS','EMPATE') and not comparable_outcome(s,now):
                unproven+=1
                verified_view.append({**s,'state':'encerrado','result':'INDISPONIVEL','settlement_pending':False})
            else:verified_view.append(s)
        return {'at':now,'operational_indicative':grouped(signals),'manual_declared':grouped(manual),
                'operational_price_verified':grouped(verified_view),
                'outcomes_without_quote_proof':unproven,
                'metric_basis':{'operational_indicative':'legacy registered outcomes; retained for compatibility, not quote verification',
                    'operational_price_verified':'exact original-source price/time evidence; unproven outcomes count as unavailable in this derived view only'},
                'history_visibility':{'archived':self.archived_history_count(),
                                      'note':'Métricas completas incluem operações retiradas do histórico visível.'},
                'counts':{'generated':generated,'telegram_confirmed_delivery':sent,'manual_executed_declared':len(manual)},
                'legacy_note':'Unversioned or price/time-incomplete legacy outcomes remain recorded historical results; they are not independently verified or reconstructed.',
                'execution_note':'Generated/published is not broker execution. Telegram delivery requires confirmation. Manual records are user declarations, never independent broker verification.'}

    def validation_page(self,now,**filters):
        with self.lock:
            return self.metric_index.page(now,**filters)

    def validation_counts(self,now):
        with self.lock:
            return self.metric_index.counts(now)

    def setup_ranking(self,now,**filters):
        from .setup_ranking import ranking
        with self.lock:
            return ranking(self.metric_index,now,**filters)

    def win_rate_snapshot(self,signal,now):
        with self.lock:
            return self.metric_index.rate_snapshot(signal,now)

    def archived_history_count(self):
        with self.lock:
            # Read-only exports may use a legacy database without this new table.
            exists=self.db.execute("SELECT 1 FROM sqlite_master WHERE name='history_archives'").fetchone()
            return self.db.execute('SELECT count(*) FROM history_archives').fetchone()[0] if exists else 0

    def _event(self,sid,kind,now,detail):
        self.db.execute('INSERT INTO events(signal_id,at,kind,detail) VALUES(?,?,?,?)',(sid,now,kind,detail))

    def patch(self,sid,changes,kind=None,now=None,*,prepare_result=False):
        with self.lock,self.db:
            row = self.db.execute('SELECT payload FROM signals WHERE id=?',(sid,)).fetchone()
            if not row: return None
            s = json.loads(row[0]); s.update(changes)
            # Opt-in at settlement/delivery only: never backfill historical messages.
            # Outcome/delivery and notification queue commit in one transaction.
            if (prepare_result and self.result_message_ready(s) and not s.get('telegram_result')
                    and s.get('telegram',{}).get('status') in ('enviado','incerto','enviando')):
                s['telegram_result']={'status':'pendente','queued_at':now if now is not None else time.time()}
                self._event(sid,'telegram resultado pendente',s['telegram_result']['queued_at'],'Resultado disponível; entrega agendada.')
            self.db.execute('UPDATE signals SET payload=? WHERE id=?',(json.dumps(s),sid))
            self.metric_index.sync(s)
            self.feedback_queue.sync(s)
            if kind: self._event(sid,kind,now if now is not None else time.time(),str(changes))
            event('signal.updated',signal_id=sid,kind=kind,state=s.get('state'),result=s.get('result'),
                  status=s.get('telegram',{}).get('status'),attempt=s.get('telegram',{}).get('attempt'))
            return s

    def signals(self,limit=100):
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute("SELECT payload FROM signals ORDER BY created DESC, json_extract(payload,'$.score') DESC, rowid ASC LIMIT ?",(limit,))]

    def learning_signals(self):
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute(
                "SELECT learning_payload FROM metric_projection WHERE scope='bot' AND price_verified=1 "
                "AND outcome IN ('WIN','LOSS') AND json_extract(metadata,'$.rules_version')=? "
                "ORDER BY created,signal_id", (RULES_VERSION,))]

    def active(self):
        # No limit: older pending outcomes must not disappear when history grows.
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute("SELECT payload FROM signals WHERE json_extract(payload,'$.state')!='cancelado' AND (json_extract(payload,'$.state')!='encerrado' OR json_extract(payload,'$.settlement_pending')=1) ORDER BY created")]

    def signal(self,sid):
        with self.lock:
            r = self.db.execute('SELECT payload FROM signals WHERE id=?',(sid,)).fetchone()
            return json.loads(r[0]) if r else None

    def analysis(self,a):
        with self.lock,self.db:
            previous=self.db.execute('SELECT payload FROM analyses WHERE id=?',(a['id'],)).fetchone()
            old_count=len(json.loads(previous[0]).get('checks',[])) if previous else 0
            self.db.execute('INSERT OR REPLACE INTO analyses VALUES(?,?,?)',(a['id'],a['created_at'],json.dumps(a)))
        event('analysis.state',analysis_id=a['id'],status=a['status'],provider=a.get('source_key'),strategy=a.get('strategy'),timeframe=a.get('timeframe','M5'),stage=a.get('stage'),reason=a.get('message'))
        for check in a.get('checks',[])[old_count:]:
            event('analysis.asset_result',analysis_id=a['id'],asset=check.get('asset'),kind=check.get('kind'),code=check.get('code'),reason=check.get('reason'),stage=check.get('stage'))

    def analyses(self):
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute('SELECT payload FROM analyses ORDER BY created DESC, rowid DESC LIMIT 100')]

    def events(self,sid):
        with self.lock:
            return [dict(r) for r in self.db.execute('SELECT * FROM events WHERE signal_id=? ORDER BY id',(sid,))]

    def settings(self,default=None):
        with self.lock,self.db:
            row = self.db.execute('SELECT payload FROM settings WHERE id=1').fetchone()
            if row: return json.loads(row[0])
            return default

    def save_settings(self,value):
        with self.lock,self.db:
            self.db.execute('INSERT OR REPLACE INTO settings VALUES(1,?)',(json.dumps(value),))
        event('settings.saved',provider=value.get('provider'),strategy=value.get('strategy'),
              limits={k:value.get(k) for k in ('hourly_limit','simultaneous_limit','loss_pause_after','pause_minutes','minimum_score')})

    def recover(self,now):
        event('recovery.started')
        # Single process supported. Never repeat an in-flight send after restart.
        with self.lock,self.db:
            self.db.execute('DELETE FROM lease')
            self.db.execute("UPDATE signals SET payload=json_set(payload,'$.telegram_result.status','incerto') WHERE json_extract(payload,'$.telegram_result.status')='enviando'")
        for s in self.active():
            if s.get('settlement_pending') and now>=s['expiry_at']+600 and not s.get('settlement_retry_until'):
                self.patch(s['id'],{'settlement_retry_until':now+600,'settlement_recovered_at':now},'recuperação da apuração',now)
        # Also repair cancelled/resolved signals: they are absent from active().
        with self.lock:
            interrupted=[json.loads(r[0]) for r in self.db.execute(
                "SELECT payload FROM signals WHERE json_extract(payload,'$.telegram.status') IN ('enviando','repetir')")]
        for s in interrupted:
            self.patch(s['id'],{'telegram':{**s['telegram'],'status':'incerto','detail':'Servidor reiniciado durante envio; sem reenvio automático.'}},'telegram',now)
        with self.lock:
            interrupted_analyses=[json.loads(r[0]) for r in self.db.execute(
                "SELECT payload FROM analyses WHERE json_extract(payload,'$.status')='analisando'")]
        for a in interrupted_analyses:
            a.update(status='interrompida',finished_at=now,recovered_at=now,
                     message='Análise interrompida por reinício; solicite nova análise.')
            self.analysis(a)

    def current_signal(self):
        with self.lock:
            visible='signal_id NOT IN (SELECT signal_id FROM history_archives)'
            order=' ORDER BY created DESC,score DESC,sequence ASC LIMIT 1'
            selected=self.db.execute("SELECT signal_id FROM signal_controls WHERE "+visible+
                " AND state IN ('aguardando entrada','em andamento')"+order).fetchone()
            if selected is None:
                selected=self.db.execute('SELECT signal_id FROM signal_controls WHERE '+visible+order).fetchone()
            if selected is None:return None
            row=self.db.execute("SELECT json_remove(payload,'$.evidence') FROM signals WHERE id=?",(selected[0],)).fetchone()
            return json.loads(row[0]) if row else None

    def history_facets(self):
        with self.lock:
            assets=[r[0] for r in self.db.execute("SELECT DISTINCT json_extract(payload,'$.asset') FROM signals WHERE id NOT IN (SELECT signal_id FROM history_archives) AND json_extract(payload,'$.asset') IS NOT NULL ORDER BY 1")]
            strategies=[r[0] for r in self.db.execute("SELECT DISTINCT COALESCE(json_extract(payload,'$.strategy_resolved'),json_extract(payload,'$.strategy')) FROM signals WHERE id NOT IN (SELECT signal_id FROM history_archives) AND COALESCE(json_extract(payload,'$.strategy_resolved'),json_extract(payload,'$.strategy')) IS NOT NULL ORDER BY 1")]
        return {'assets':assets,'strategies':strategies}

    def history_days(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        counts={};tz=ZoneInfo('America/Sao_Paulo')
        with self.lock:
            rows=self.db.execute("SELECT json_extract(payload,'$.entry_at') FROM signals WHERE id NOT IN (SELECT signal_id FROM history_archives) AND json_extract(payload,'$.entry_at') IS NOT NULL")
            for row in rows:
                day=datetime.fromtimestamp(row[0],tz).date().isoformat()
                counts[day]=counts.get(day,0)+1
        return [{'date':day,'signals':n} for day,n in sorted(counts.items(),reverse=True)]

    def history_day_summary(self, day):
        """Aggregate all visible signals for a São Paulo calendar day, without pagination.

        WIN/LOSS counts remain *indicative* and must not be presented as executed P&L.
        """
        from datetime import datetime, timedelta, time
        from zoneinfo import ZoneInfo
        tz=ZoneInfo('America/Sao_Paulo')
        start=datetime.combine(day,time.min,tzinfo=tz).timestamp()
        end=datetime.combine(day+timedelta(days=1),time.min,tzinfo=tz).timestamp()
        counts={'total':0,'wins':0,'losses':0,'ties':0,'unavailable':0,'pending':0,'cancelled':0}
        with self.lock:
            rows=self.db.execute("SELECT json_extract(payload,'$.state') AS state, "
                "json_extract(payload,'$.result') AS result, "
                "json_extract(payload,'$.settlement_pending') AS settlement_pending "
                "FROM signals WHERE id NOT IN (SELECT signal_id FROM history_archives) "
                "AND json_extract(payload,'$.entry_at')>=? AND json_extract(payload,'$.entry_at')<?",(start,end))
            for row in rows:
                counts['total']+=1
                if row['state']=='cancelado':counts['cancelled']+=1
                elif row['state']!='encerrado' or row['settlement_pending']:
                    counts['pending']+=1
                elif row['result']=='WIN':counts['wins']+=1
                elif row['result']=='LOSS':counts['losses']+=1
                elif row['result']=='EMPATE':counts['ties']+=1
                else:counts['unavailable']+=1
        observations=counts['wins']+counts['losses']
        return {'date':day.isoformat(),'timezone':'America/Sao_Paulo',**counts,
                'win_rate_pct':round(counts['wins']*100/observations,2) if observations else None,
                'basis':'Sinais visíveis; apuração indicativa, não resultado de operações executadas.'}

    def export_period(self,start,end):
        """Full original payloads for the requested day, including archived rows."""
        with self.lock:
            rows=self.db.execute("SELECT payload FROM signals WHERE json_extract(payload,'$.entry_at')>=? "
                "AND json_extract(payload,'$.entry_at')<? ORDER BY created,rowid",(start,end))
            signals=[json.loads(row[0]) for row in rows]
            confirmations=[c for signal in signals for c in self.manual_confirmations(signal['id'])]
            return signals,confirmations

    def history(self,page=1,page_size=20,asset=None,source=None,direction=None,state=None,start=None,end=None,snapshot_id=None,strategy=None):
        conditions=['id NOT IN (SELECT signal_id FROM history_archives)'];args=[]
        for field,value in [('asset',asset),('source_key',source),('direction',direction),('state',state)]:
            if value:
                conditions.append("json_extract(payload,'$."+field+"')=?");args.append(value)
        if strategy:
            conditions.append("COALESCE(json_extract(payload,'$.strategy_resolved'),json_extract(payload,'$.strategy'))=?");args.append(strategy)
        if start is not None: conditions.append("json_extract(payload,'$.entry_at')>=?");args.append(start)
        if end is not None: conditions.append("json_extract(payload,'$.entry_at')<?");args.append(end)
        with self.lock:
            # Freeze membership across pages while outcomes can keep updating.
            # Signals are never deleted, so the insertion rowid is a stable watermark.
            if snapshot_id is None:
                snapshot_id=self.db.execute('SELECT COALESCE(MAX(rowid),0) FROM signals').fetchone()[0]
            conditions.append('rowid<=?');args.append(snapshot_id)
            where=' WHERE '+' AND '.join(conditions)
            total=self.db.execute('SELECT count(*) FROM signals'+where,args).fetchone()[0]
            rows=self.db.execute("SELECT json_remove(payload,'$.evidence') FROM signals"+where+' ORDER BY created DESC, rowid DESC LIMIT ? OFFSET ?',[*args,page_size,(page-1)*page_size])
            return {'items':[json.loads(r[0]) for r in rows],'total':total,'page':page,'page_size':page_size,'snapshot_id':snapshot_id}
