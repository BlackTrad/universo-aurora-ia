"""Rebuildable, compact metric projections; originals remain the authority.

Each mutation updates its projection in the same SQLite transaction. Pages load
only their groups, without candle arrays. Historical signal text is never backfilled.
"""
import hashlib
import json
import math
from .validation_metrics import canonical, GROUP_FIELDS, summarize, result_of
from .outcome_evidence import comparable_outcome, verified_projection, PROOF_VERSION

SCHEMA_VERSION = '1.26.2-1'
MIN_RATE_SAMPLE = 30
FIELDS = ('id','created_at','entry_at','expiry_at','settled_at','state','result',
          'settlement_pending','payout_pct','payout_kind','entry_price','expiry_price',
          'settlement_source_identity','settlement_entry_at','settlement_expiry_at',
          'telegram','broker','recorded_at','direction','confirmation_id','manual_executed','apex_setup',
          'price_evidence','planned_duration_seconds','duration_basis','planned_entry_at','planned_expiry_at')


def manual_view(signal, manual):
    """Project declared execution; never borrow indicative prices as broker prices."""
    row = canonical(signal)
    entry, expiry = manual.get('entry_at'), manual.get('expiry_at')
    actual_duration = expiry - entry if entry is not None and expiry is not None else None
    return {**row, 'result':manual.get('result'),
            'state':'encerrado' if manual.get('executed') else 'not_executed', 'settlement_pending':False,
            'settled_at':manual['recorded_at'], 'recorded_at':manual['recorded_at'],
            'confirmation_id':manual['confirmation_id'], 'manual_executed':bool(manual.get('executed')),
            'payout_pct':manual.get('payout_pct'), 'payout_kind':'manual_user_declared',
            'broker':manual.get('broker','não informada'),
            'planned_duration_seconds':row.get('duration_seconds'),
            'planned_entry_at':row.get('entry_at'), 'planned_expiry_at':row.get('expiry_at'),
            'duration_seconds':actual_duration if actual_duration is not None else row.get('duration_seconds'),
            'duration_basis':'manual_actual' if actual_duration is not None else 'planned_unconfirmed',
            'entry_at':entry if entry is not None else row.get('entry_at'),
            'expiry_at':expiry if expiry is not None else row.get('expiry_at'),
            'entry_price':manual.get('entry_price'), 'expiry_price':manual.get('expiry_price'),
            'settlement_evidence':None, 'price_evidence':None}


def projection(signal, manual=None):
    row=canonical(signal)
    if manual is not None:
        row=manual_view(signal,manual)
    else:
        row['broker']='fonte externa'
        verified=comparable_outcome(signal,math.inf)
        quotes=signal.get('settlement_evidence')
        row['price_evidence']={'version':PROOF_VERSION,'verified':verified,
            'quote_digest':hashlib.sha256(json.dumps(quotes,sort_keys=True,allow_nan=False).encode()).hexdigest() if verified else None}
    row={k:row.get(k) for k in (*GROUP_FIELDS,*FIELDS)}
    # Invalid historical numbers stay invalid in the original. The derived
    # index must not prevent an upgrade or serialize NaN as a valid price.
    row={k:(None if isinstance(v,float) and not math.isfinite(v) else v) for k,v in row.items()}
    for k in ('created_at','entry_at','expiry_at','settled_at','recorded_at','entry_price','expiry_price'):
        if row.get(k) is None:row.pop(k,None)
    # Only delivery state is needed; never replicate raw credential metadata.
    row['telegram']={k:(row.get('telegram') or {}).get(k) for k in ('status','at')}
    metadata={k:row.get(k,'unknown') for k in GROUP_FIELDS}
    metadata['broker']=row['broker']
    encoded=json.dumps(metadata,sort_keys=True,ensure_ascii=False,allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest(),metadata,row


class ValidationIndex:
    def __init__(self,store):
        self.store=store;self.db=store.db
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS metric_projection(
            signal_id TEXT NOT NULL, scope TEXT NOT NULL, group_id TEXT NOT NULL,
            metadata TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL,
            PRIMARY KEY(signal_id,scope));
        CREATE INDEX IF NOT EXISTS idx_metric_group ON metric_projection(scope,group_id,created);
        CREATE INDEX IF NOT EXISTS idx_metric_created ON metric_projection(scope,created);
        CREATE INDEX IF NOT EXISTS idx_metric_manual ON metric_projection(scope,json_extract(payload,'$.id'),json_extract(payload,'$.confirmation_id'));
        CREATE TABLE IF NOT EXISTS metric_projection_version(id INTEGER PRIMARY KEY CHECK(id=1), version TEXT);
        ''')
        columns={r[1] for r in self.db.execute('PRAGMA table_info(metric_projection)')}
        for name,definition in (('outcome',"TEXT NOT NULL DEFAULT 'PENDENTE'"),
                                ('resolved_at','REAL'),('price_verified','INTEGER NOT NULL DEFAULT 0'),('learning_payload','TEXT')):
            if name not in columns:
                self.db.execute('ALTER TABLE metric_projection ADD COLUMN '+name+' '+definition)
        version=self.db.execute('SELECT version FROM metric_projection_version WHERE id=1').fetchone()
        if not version or version[0]!=SCHEMA_VERSION:
            with self.db:
                self.db.execute('DELETE FROM metric_projection')
                for r in self.db.execute('SELECT payload FROM signals'):
                    self.sync(json.loads(r[0]))
                for r in self.db.execute('SELECT m.id,m.payload,s.payload AS signal FROM manual_confirmations m JOIN signals s ON s.id=m.signal_id ORDER BY m.id'):
                    self._put(json.loads(r['signal']),'manual',{'confirmation_id':r['id'],**json.loads(r['payload'])})
                self.db.execute('INSERT OR REPLACE INTO metric_projection_version VALUES(1,?)',(SCHEMA_VERSION,))
        self.db.commit()

    def sync(self,signal):
        self._put(signal,'bot')
        latest=self.db.execute('SELECT id,payload FROM manual_confirmations WHERE signal_id=? ORDER BY id DESC LIMIT 1',
                               (signal['id'],)).fetchone()
        if latest:self._put(signal,'manual',{'confirmation_id':latest['id'],**json.loads(latest['payload'])})

    def _put(self,signal,scope,manual=None):
        item=projection(signal,manual)
        group_id,metadata,row=item
        from .strategies.presentation import strategy_label
        metadata={**metadata,'strategy_label':strategy_label(row)}
        key=signal['id'] if scope=='bot' else signal['id']+':'+str(manual['confirmation_id'])
        # Keep only two original settlement quotes for learning, with strict
        # raw proof validation. The decision's hundred candles stay in originals.
        learning=None
        if scope=='bot' and verified_projection(row,math.inf):
            evidence=signal.get('evidence') or {}
            learning={**row,'strategy_resolved':signal.get('strategy_resolved',signal.get('strategy')),
                'settlement_evidence':signal['settlement_evidence'],
                'evidence':{k:evidence.get(k) for k in ('rules_version','pipeline_version','regime')},
                'emitted_at':signal.get('emitted_at')}
            learning['evidence']['configuration']={'id':(evidence.get('configuration') or {}).get('id')}
        self.db.execute('INSERT OR REPLACE INTO metric_projection '
            '(signal_id,scope,group_id,metadata,payload,created,outcome,resolved_at,price_verified,learning_payload) VALUES(?,?,?,?,?,?,?,?,?,?)',
            (key,scope,group_id,json.dumps(metadata,ensure_ascii=False,allow_nan=False),
             json.dumps(row,ensure_ascii=False,allow_nan=False),row.get('created_at',0),result_of(row),
             row.get('settled_at'),int(scope=='bot' and verified_projection(row,math.inf)),
             json.dumps(learning,ensure_ascii=False,allow_nan=False) if learning else None))

    @staticmethod
    def at(row,now):
        # Missing settlement timestamps are historical records, never proofs of
        # what was known at an earlier cutoff.
        if (row.get('settled_at') or 0)>now:
            return {**row,'settlement_pending':True}
        return row

    @staticmethod
    def manual_condition():
        return ("json_extract(payload,'$.manual_executed')=1 AND json_extract(payload,'$.recorded_at')<=? "
                "AND NOT EXISTS(SELECT 1 FROM metric_projection newer WHERE newer.scope='manual' "
                "AND json_extract(newer.payload,'$.id')=json_extract(metric_projection.payload,'$.id') "
                "AND json_extract(newer.payload,'$.confirmation_id')>json_extract(metric_projection.payload,'$.confirmation_id') "
                "AND json_extract(newer.payload,'$.recorded_at')<=?)")

    def counts(self,now):
        bot=self.db.execute("SELECT count(*),sum(json_extract(payload,'$.telegram.status')='enviado' "
            "AND COALESCE(json_extract(payload,'$.telegram.at'),0)<=?) FROM metric_projection WHERE scope='bot' AND created<=?",(now,now)).fetchone()
        manual=self.db.execute("SELECT count(*) FROM metric_projection WHERE scope='manual' AND created<=? AND "+self.manual_condition(),(now,now,now)).fetchone()[0]
        return {'generated':bot[0],'telegram_confirmed_delivery':bot[1] or 0,'manual_executed_declared':manual}

    def page(self,now,*,scope='bot',page=1,page_size=10,source='',strategy='',query=''):
        if scope not in ('bot','manual'):raise ValueError('Escopo de métricas inválido.')
        if not 1<=page<=1000000 or not 1<=page_size<=50:raise ValueError('Página inválida.')
        where=['scope=?','created<=?'];args=[scope,now]
        if scope=='manual':where.append(self.manual_condition());args.extend((now,now))
        for key,value in [('source_key',source),('strategy',strategy)]:
            if value:where.append("json_extract(metadata,'$."+key+"')=?");args.append(value)
        if query.strip():
            # Binding plus escaped LIKE metacharacters: filters do not execute SQL.
            value=query.strip().lower().replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
            where.append("lower(metadata) LIKE ? ESCAPE '\\'");args.append('%'+value+'%')
        condition=' AND '.join(where)
        total=self.db.execute('SELECT count(DISTINCT group_id) FROM metric_projection WHERE '+condition,args).fetchone()[0]
        actual=min(page,max(1,math.ceil(total/page_size)))
        # Hash is a stable tie-breaker; pagination never uses changing rate rankings.
        groups=self.db.execute('SELECT group_id,metadata FROM metric_projection WHERE '+condition+
            ' GROUP BY group_id ORDER BY metadata,group_id LIMIT ? OFFSET ?',(*args,page_size,(actual-1)*page_size)).fetchall()
        items=[]
        for g in groups:
            rows=[self.at(json.loads(r[0]),now) for r in self.db.execute(
                'SELECT payload FROM metric_projection WHERE '+condition+' AND group_id=?',(*args,g['group_id']))]
            summary=summarize(rows)
            quality={}
            if scope=='bot':
                verified=[r for r in rows if verified_projection(r,now)]
                unverified=sum(result_of(r) in ('WIN','LOSS','EMPATE') for r in rows)-len(verified)
                verified_view=[{**r,'result':'INDISPONIVEL','state':'encerrado','settlement_pending':False}
                    if result_of(r) in ('WIN','LOSS','EMPATE') and not verified_projection(r,now) else r for r in rows]
                stats=summarize(verified_view) if unverified else summary
                quality={'legacy_unverified_outcomes':unverified,
                         'price_verified':stats,
                         'rate_basis':'exact_original_source_quotes; original registered counts are retained separately'}
            items.append({'group_id':g['group_id'],**json.loads(g['metadata']),**summary,**quality})
        facets={}
        for key in ('source_key','strategy'):
            values=self.db.execute("SELECT DISTINCT json_extract(metadata,'$."+key+"') FROM metric_projection "
                'WHERE scope=? AND created<=? ORDER BY 1',(scope,now))
            facets[key]=[r[0] for r in values if r[0]]
        return {'items':items,'total':total,'page':actual,'page_size':page_size,'facets':facets,'at':now,
                'scope':scope,'pagination':'server','projection_version':SCHEMA_VERSION}

    def rate_snapshot(self,signal,now):
        item=projection(signal)
        group_id,metadata,_=item
        rows=[];excluded=0
        for r in self.db.execute("SELECT payload FROM metric_projection WHERE scope='bot' AND group_id=? AND created<=?",(group_id,now)):
            row=json.loads(r[0])
            if verified_projection(row,now):rows.append(row)
            else:excluded+=1
        values=summarize(rows)
        sample=values['evaluated']
        return {'version':'1.26.2-observed-1','kind':'historical_observed','basis':'indicative_external',
                'recorded_at':now,'context':metadata,'group_id':group_id,'minimum_sample':MIN_RATE_SAMPLE,
                'sample':sample,'wins':values['wins'],'losses':values['losses'],'ties':values['ties'],
                'rate_pct':values['observed_rate_pct'] if sample>=MIN_RATE_SAMPLE else None,
                'wilson_95_pct':values['wilson_95_pct'] if sample else None,
                'day_block_95_pct':values['day_block_95_pct'],'resolved_days':values['resolved_days'],
                'first_entry_at':values['first_signal_at'],'last_expiry_at':values['last_expiry_at'],
                'excluded_incomplete_or_unknown':excluded,
                'status':'descriptive' if sample>=MIN_RATE_SAMPLE else 'insufficient',
                'denominator':'WIN + LOSS; empates e indisponíveis excluídos',
                'probability_estimate':False,
                'note':'Histórico da mesma combinação; não estima a chance da próxima entrada.'}


