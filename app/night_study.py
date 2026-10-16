"""Forward paper observations only: no orders, Telegram or operational risk state."""
import asyncio
import csv
import json
import math
import sqlite3
import time
from datetime import datetime,timedelta
from pathlib import Path
from .logging_setup import BRASILIA,event
from .market import validate,DataError,valid_candle
from .strategies import STRATEGIES
from .learning import RULES_VERSION, profiles, study_records


def window(now):
    local=datetime.fromtimestamp(now,BRASILIA)
    start=local.replace(hour=22,minute=0,second=0,microsecond=0)
    if local.hour<6:start-=timedelta(days=1)
    return start.timestamp(),(start+timedelta(hours=8)).timestamp()


class Study:
    def __init__(self,path):
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.path=path
        self.db=sqlite3.connect(path)
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS observations(
          id INTEGER PRIMARY KEY, night TEXT, fingerprint TEXT UNIQUE, asset TEXT,
          strategy TEXT, direction TEXT, entry_at INTEGER, expiry_at INTEGER,
          score REAL, evidence TEXT, result TEXT DEFAULT 'PENDENTE',
          entry_price REAL, expiry_price REAL, settled_at REAL)''')
        self.db.commit()

    def count(self,night):
        return self.db.execute('SELECT count(*) FROM observations WHERE night=?',(night,)).fetchone()[0]

    def add(self,night,asset,strategy,candidate,candles,now,limit=100,source_identity=None):
        entry=math.ceil((now+60)/60)*60
        scope="continuous" if night.startswith("continuous-") else night
        identity=f"{scope}|{asset}|{strategy}|{candidate['direction']}|{candidate['setup_candle']}"
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if self.count(night)>=limit:return False
            cur=self.db.execute('''INSERT OR IGNORE INTO observations
              (night,fingerprint,asset,strategy,direction,entry_at,expiry_at,score,evidence)
              VALUES(?,?,?,?,?,?,?,?,?)''',(night,identity,asset,strategy,candidate['direction'],entry,entry+300,
              candidate['score'],json.dumps({'candidate':candidate,'candles':[c.json() for c in candles],'created_at':now,'source_identity':source_identity,'rules_version':RULES_VERSION})))
            return cur.rowcount==1

    def pending(self):
        return [dict(row) for row in self.db.execute("SELECT * FROM observations WHERE result='PENDENTE'")]

    def settle(self,row,candles,now):
        if now<row['expiry_at']+65:return
        quotes={c.t:c for c in candles if valid_candle(c) and isinstance(c.t,int) and c.t%60==0 and c.t+60<=now}
        # Duplicate epochs are ambiguous, even if two values happen to match.
        if len({c.t for c in candles})!=len(candles):quotes={}
        opening=quotes.get(row['entry_at']);closing=quotes.get(row['expiry_at'])
        if opening and closing:
            result='EMPATE' if opening.o==closing.o else ('WIN' if (closing.o>opening.o)==(row['direction']=='CALL') else 'LOSS')
        elif now>=row['expiry_at']+600:result='INDISPONIVEL'
        else:return
        with self.db:
            self.db.execute("UPDATE observations SET result=?,entry_price=?,expiry_price=?,settled_at=? WHERE id=? AND result='PENDENTE'",
                (result,opening.o if opening else None,closing.o if closing else None,now,row['id']))
        event('study.settled',signal_id=str(row['id']),asset=row['asset'],strategy=row['strategy'],result=result)

    def export(self,night,folder):
        folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
        rows=[dict(row) for row in self.db.execute('SELECT * FROM observations WHERE night=? ORDER BY id',(night,))]
        fields=['id','asset','strategy','direction','entry_at','expiry_at','score','result','entry_price','expiry_price']
        with (folder/(night+'-entradas.csv')).open('w',newline='',encoding='utf-8-sig') as f:
            writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader()
            for row in rows:
                writer.writerow({**row,**{k:datetime.fromtimestamp(row[k],BRASILIA).isoformat() for k in ('entry_at','expiry_at')}})
        summary=[]
        for key in STRATEGIES:
            if key in ('scan_all','auto'):continue
            outcomes=[r['result'] for r in rows if r['strategy']==key]
            wins=outcomes.count('WIN');losses=outcomes.count('LOSS');sample=wins+losses
            summary.append({'strategy':key,'observations':len(outcomes),'wins':wins,'losses':losses,
                'ties':outcomes.count('EMPATE'),'pending':outcomes.count('PENDENTE'),
                'unavailable':outcomes.count('INDISPONIVEL'),'sample':sample,
                'win_rate_pct':round(100*wins/sample,2) if sample else None,
                'sample_note':'amostra pequena' if sample<30 else 'resultado indicativo'})
        report={'night':night,'mode':'simulação prospectiva M5, expiração 5 minutos',
                'target':100,'observations':len(rows),'strategies':summary,
                'learning_profiles':[{'context':list(key),**value} for key,value in profiles(study_records(self.path),time.time()).items()],
                'limits':'Simulações simultâneas e correlacionadas; sem ordens, payout ou confirmação da corretora. Não representam a carteira operacional com limite de 10 sinais/hora. Ausência de dados pode impedir atingir 100.'}
        (folder/(night+'-resumo.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        return report


async def cycle(study,provider,assets,night,end,clock=time.time):
    # Recover outcomes from preceding runs before collecting new observations.
    pending=study.pending()
    for asset in dict.fromkeys(r['asset'] for r in pending if clock()>=r['expiry_at']+65):
        try:quotes=await provider.candles(asset,60,100)
        except DataError as error:
            event('study.data_error',asset=asset,code=error.code,reason=str(error));quotes=[]
        for row in pending:
            if row['asset']==asset:study.settle(row,quotes,clock())
    for asset in assets:
        if study.count(night)>=100 or clock()>=end:break
        try:
            await provider.check_asset(asset)
            candles=await provider.candles(asset,300,100)
            received=clock()
            closed=validate(candles,received,330,300)
            if closed[-1].t+300 != int(received//300)*300:
                raise DataError('A última vela fechada ainda não foi recebida.','missing_latest_candle')
            for key,evaluate in list(STRATEGIES.items()):
                if key in ('auto','scan_all'):continue
                candidate,reason=evaluate(closed)
                if not candidate or candidate.get('score',0)<70:continue
                now=clock()
                if now>=end:break
                validate(candles,now,330,300)
                if int(now//300)!=int(received//300):
                    raise DataError('Uma nova vela fechou durante o estudo.','candle_changed')
                if study.add(night,asset,key,candidate,closed,now,source_identity=getattr(candles,'source_identity',getattr(provider,'identity',provider.name))):
                    event('study.observed',asset=asset,strategy=key,direction=candidate['direction'],score=candidate['score'])
        except DataError as error:
            event('study.data_error',asset=asset,code=error.code,reason=str(error))


def current_batch(study):
    row=study.db.execute("SELECT night FROM observations WHERE night LIKE 'continuous-%' ORDER BY id DESC LIMIT 1").fetchone()
    key=row[0] if row else 'continuous-000001'
    if study.count(key)>=100:
        key='continuous-'+str(int(key.split('-')[1])+1).zfill(6)
    return key


async def run_continuous(study,provider,assets,folder,status=None):
    """Owned by backend lifespan; persistent batches of 100 paper observations."""
    status=status if status is not None else {}
    status.update(active=True,mode='simulado',message='Estudo contínuo ativo; aguardando próximo ciclo.')
    try:
        while True:
            # Space requests and let foreground startup complete before first collection.
            await asyncio.sleep(30)
            batch=current_batch(study)
            previous={row['night'] for row in study.pending()}
            try:
                selected=assets() if callable(assets) else assets
                await asyncio.wait_for(cycle(study,provider,selected,batch,float('inf')),180)
                for key in previous|{batch}:study.export(key,folder)
                status.update(batch=batch,observations=study.count(batch),last_cycle_at=time.time(),
                    message='Coletando e apurando observações simuladas enquanto o bot está ligado.')
                event('study.cycle_completed',count=study.count(batch),status='active')
            except Exception:
                status.update(message='Falha no ciclo do estudo; nova tentativa automática.')
                event('study.cycle_failed',exc_info=True)
    finally:
        status.update(active=False,message='Estudo parado com o bot; registros preservados.')
