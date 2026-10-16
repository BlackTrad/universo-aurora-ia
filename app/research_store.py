"""Prospective observations in a separate DB. No order, signal or Telegram API."""
import asyncio
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import threading
from dataclasses import asdict
from functools import lru_cache
from .market import DataError, valid_candle
from .strategies.quality import entry_guard
from .logging_setup import event
from .experiments import RULES
from .research_filters import PARAMETERS


@lru_cache(maxsize=1)
def code_fingerprint():
    root=Path(__file__).resolve().parent;digest=hashlib.sha256()
    for p in sorted(root.rglob('*.py')):
        digest.update(str(p.relative_to(root)).encode());digest.update(p.read_bytes())
    return digest.hexdigest()


class ResearchStore:
    def __init__(self,path):
        if path!=':memory:':Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(path,check_same_thread=False)
        self.lock=threading.RLock();self.closed=False
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS observations(id TEXT PRIMARY KEY, created REAL NOT NULL, payload TEXT NOT NULL)')
        self.db.execute("CREATE INDEX IF NOT EXISTS research_state ON observations(json_extract(payload,'$.state'),created)")
        self.db.commit()
        self.live_quotes={}

    def watch_price(self,key,point,now):
        from .entry_watch import check_live_entry
        for row in self.pending():
            if not row.get('live_monitor_required') or now>=row['entry_at'] or (row['source_key'],row['asset'])!=key:continue
            try:audit,reason=check_live_entry(row,point,now)
            except DataError:continue
            if reason:self.patch(row,state='cancelled',result='CANCELADO',settled_at=now,cancellation_reason=reason,entry_validation=audit)
            else:self.live_quotes[row['id']]=point

    def stream_gap(self,key,now,code):
        for row in self.pending():
            if row.get('live_monitor_required') and now<row['entry_at'] and (row['source_key'],row['asset'])==key:
                self.patch(row,state='cancelled',result='CANCELADO',settled_at=now,
                           cancellation_reason='Continuidade da cotação perdida antes da entrada: '+code)

    def close(self):
        with self.lock:
            if not self.closed:
                self.db.close();self.closed=True

    def rows(self):
        with self.lock:
            return [json.loads(row[0]) for row in self.db.execute('SELECT payload FROM observations ORDER BY created,id')]

    def pending(self):
        with self.lock:
            return [json.loads(row[0]) for row in self.db.execute("SELECT payload FROM observations WHERE json_extract(payload,'$.state') IN ('pending_validation','pending_result') ORDER BY created,id")]

    def patch(self,record,**updates):
        record={**record,**updates}
        if record.get('state') in ('cancelled','settled','unavailable') or record.get('entry_watch_complete'):
            self.live_quotes.pop(record['id'],None)
        with self.lock,self.db:self.db.execute('UPDATE observations SET payload=? WHERE id=?',(json.dumps(record,ensure_ascii=False,allow_nan=False),record['id']))
        return record

    def capture(self,research,*,asset,source_key,source_identity,rows,interval,duration,now,observed_at,min_lead=60,variant='base',payout_pct=None):
        accepted=research['accepted'];setup=research.get('setup_identity',str(rows[-1].t))
        # One prediction per movement and experimental combination, including after restart.
        fingerprint=code_fingerprint()
        identity=(research['version'],fingerprint,source_identity,asset,interval,duration,research['strategy'],variant,
                  setup if accepted else str(rows[-1].t))
        rid=hashlib.sha256(repr(identity).encode()).hexdigest()
        entry=math.ceil((now+min_lead)/60)*60
        if accepted and research.get('entry_policy')=='next_analysis_candle':entry=research['target_entry_at']
        payload={**research,'id':rid,'variant':variant,'asset':asset,'source_key':source_key,
                 'source_identity':source_identity,'created_at':now,'observed_at':observed_at,'interval_seconds':interval,
                 'duration_seconds':duration,'entry_at':entry,'expiry_at':entry+duration,
                 'duration_reason':'Duração escolhida pelo usuário no painel; não inferida pela estratégia.',
                 'result_kind':'experimental_indicativo','broker_confirmed':False,'sent_to_telegram':False,
                 'payout_pct':payout_pct,'payout_kind':'declarado_no_painel' if payout_pct is not None else 'indisponível',
                 'research_configuration':{'rules':asdict(RULES),'rule_snapshot':research.get('rule_snapshot'),
                                           'filter_parameters':PARAMETERS,'code_sha256':fingerprint},
                 'evidence':{'candles':[c.json() for c in rows],'source_identity':source_identity},
                 'result':None,'state':'pending_validation' if accepted else 'rejected'}
        if accepted:
            payload['entry_validation']={'status':'pendente','atr':research['atr'],'scheduled_at':entry-45}
            if research.get('entry_policy')=='next_analysis_candle':
                payload['entry_timing']={'policy':'next_analysis_candle','target_entry_at':entry,
                    'entry_window_end':research['entry_window_end'],'decision_at':now,'observed_at':observed_at,
                    'lead_seconds':entry-observed_at,'latency_seconds':max(0,observed_at-entry),
                    'minimum_lead_seconds':min_lead,'actionable':observed_at<=entry-min_lead}
            if observed_at>entry-min_lead or entry>research['valid_until']:
                payload.update(state='cancelled',result='CANCELADO',settled_at=observed_at,
                               cancellation_reason=('Abertura da próxima vela sem antecedência após confirmar fechamento; referência preservada, sem remarcar entrada.'
                                  if research.get('entry_policy')=='next_analysis_candle' else 'Antecedência insuficiente ou setup vencido ao registrar observação.'))
        try:
            with self.lock,self.db:self.db.execute('INSERT INTO observations VALUES(?,?,?)',(rid,now,json.dumps(payload,ensure_ascii=False,allow_nan=False)))
        except sqlite3.IntegrityError:return None
        return payload

    async def step(self,providers,clock):
        """One bounded request per step. Operational tasks have scheduler priority."""
        now=clock()
        from .entry_watch import check_live_entry
        for row in self.pending():
            if (row.get('live_monitor_required') and row['state']=='pending_result' and
                    now>=row['entry_at'] and not row.get('entry_watch_complete')):
                try:
                    point=self.live_quotes.get(row['id'])
                    audit,reason=check_live_entry(row,point,row['entry_at'])
                except (DataError,AttributeError):audit={};reason='Cotação original recente indisponível antes da entrada observada.'
                if reason:self.patch(row,state='cancelled',result='CANCELADO',settled_at=now,cancellation_reason=reason,entry_validation=audit)
                else:self.patch(row,entry_watch_complete=True,entry_watch=audit)
        pending=[r for r in self.pending() if r['state']=='pending_validation']
        due=[]
        for r in pending:
            if now>=r['entry_at']-15:
                self.patch(r,state='cancelled',result='CANCELADO',settled_at=now,
                           cancellation_reason='Revalidação prospectiva não concluída no prazo; sem confirmação retrospectiva.')
            elif now>=r['entry_at']-45:due.append(r)
        if due:
            first=min(due,key=lambda r:r['entry_at']);provider=providers.get(first['source_key'])
            group=[r for r in due if (r['source_key'],r['asset'],r['source_identity'])==
                   (first['source_key'],first['asset'],first['source_identity'])]
            try:
                if provider is None:raise DataError('Fonte original indisponível.')
                quotes=await asyncio.wait_for(provider.candles(first['asset'],60,4),8)
                identity=getattr(quotes,'source_identity',getattr(provider,'identity',provider.name))
                if identity!=first['source_identity']:raise DataError('Fonte original divergente.')
                point=None
                if any(r.get('manual_intrabar') for r in group):
                    point=await asyncio.wait_for(provider.live_price(first['asset']),8)
                for r in group:
                    checked_at=clock();audit,reason=entry_guard(r,quotes,checked_at)
                    if not reason and r.get('manual_intrabar'):
                        live_audit,reason=check_live_entry(r,point,checked_at);audit.update(live_audit)
                        if not reason:self.live_quotes[r['id']]=point
                    last=[c for c in quotes if c.t+60<=checked_at][-1]
                    limits=r.get('invalidation',[])
                    limit=limits[0] if limits else None
                    cutoff=r['evidence']['candles'][-1]['t']+r['interval_seconds']
                    subsequent=[c for c in quotes if c.t>=cutoff and c.t+60<=checked_at]
                    violations=[]
                    if r.get('invalidation_line'):
                        from .third_touch import invalidation_limit
                        limit=invalidation_limit(r['invalidation_line'],last.t)
                        audit={**audit,'experimental_invalidation_limit':limit,'experimental_invalidation_at':last.t}
                    if limit is not None:
                        for c in subsequent:
                            level=invalidation_limit(r['invalidation_line'],c.t) if r.get('invalidation_line') else limit
                            if (c.c<level if r['direction']=='CALL' else c.c>level):
                                violations.append({'candle_at':c.t,'close':c.c,'limit':level})
                        audit={**audit,'experimental_invalidation_violations':violations,
                               'experimental_checked_candles':[c.t for c in subsequent]}
                    if not reason and violations:
                        reason='Região experimental invalidada antes da entrada.'
                    if reason:self.patch(r,state='cancelled',result='CANCELADO',settled_at=checked_at,cancellation_reason=reason,entry_validation=audit)
                    else:self.patch(r,state='pending_result',entry_validation={**audit,'status':'confirmada'})
            except Exception:
                for r in group:self.patch(r,state='cancelled',result='CANCELADO',settled_at=clock(),cancellation_reason='Dados de revalidação prospectiva indisponíveis ou prazo insuficiente.')
            return
        settled=[r for r in self.pending() if r['state']=='pending_result' and now>=r['expiry_at']+65
                 and now-r.get('last_attempt',0)>=60]
        if not settled:return
        first=min(settled,key=lambda r:r['expiry_at']);provider=providers.get(first['source_key'])
        group=[r for r in settled if (r['source_key'],r['asset'],r['source_identity'],r['entry_at'],r['expiry_at'])==
               (first['source_key'],first['asset'],first['source_identity'],first['entry_at'],first['expiry_at'])]
        try:
            if provider is None:raise DataError('Fonte original indisponível.')
            if hasattr(provider,'settlement_candles'):
                query=provider.settlement_candles(first['asset'],first['entry_at'],first['expiry_at'])
            else:query=provider.candles(first['asset'],60,100)
            quotes=await asyncio.wait_for(query,8)
            identity=getattr(quotes,'source_identity',getattr(provider,'identity',provider.name))
            if identity!=first['source_identity']:raise DataError('Fonte original divergente.')
            if (any(not valid_candle(c) or type(c.t) is not int or c.t<=0 or c.t%60 or c.t>clock() for c in quotes)
                    or any(b.t<=a.t for a,b in zip(quotes,quotes[1:]))):raise DataError('Candles inválidos.')
            by_time={c.t:c for c in quotes};opening=by_time.get(first['entry_at']);closing=by_time.get(first['expiry_at'])
            if opening is None or closing is None or closing.t+60>clock():raise DataError('Preços exatos indisponíveis.')
            for r in group:
                outcome='EMPATE' if closing.o==opening.o else 'WIN' if (closing.o>opening.o)==(r['direction']=='CALL') else 'LOSS'
                self.patch(r,state='settled',result=outcome,settled_at=clock(),entry_price=opening.o,expiry_price=closing.o,
                           settlement_evidence=[opening.json(),closing.json()],settlement_source_identity=identity)
                event('experiment.prospective_settled',asset=r['asset'],strategy=r['strategy'],result=outcome,
                      source_identity=identity,entry_at=r['entry_at'],expiry_at=r['expiry_at'])
        except Exception:
            for r in group:
                updates={'last_attempt':clock(),'last_error':'Sem cotações exatas válidas da fonte original.'}
                if clock()>=r['expiry_at']+600:updates.update(state='unavailable',result='INDISPONIVEL',settled_at=clock())
                self.patch(r,**updates)
