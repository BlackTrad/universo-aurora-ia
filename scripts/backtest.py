"""Historical OHLC replay of the AURORA strategies; no orders or Telegram calls."""
import argparse
import asyncio
from collections import Counter, defaultdict
import csv
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sys
import time
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.config import Config
from app.version import VERSION
from app.learning import RULES_VERSION, PIPELINE_VERSION
from app.strategies import SELECTOR_VERSION
from app.research_store import code_fingerprint
from app.deriv import DerivProvider
from app.market import Candle, DataError, validate, valid_candle, aggregate_minutes as aggregate
from app.strategies import STRATEGIES
from app.timeframes import TIMEFRAMES

BRASILIA=ZoneInfo('America/Sao_Paulo')

def stamp(value):
    return datetime.fromtimestamp(value,BRASILIA).isoformat()


def parse_history_candle(row,cursor):
    """Reject bad history with a precise reason; never repair prices silently."""
    if not isinstance(row,dict):
        raise DataError('Candle não é um objeto.','invalid_history_row')
    values={}
    for field in ('epoch','open','high','low','close','volume'):
        value=row.get(field)
        if field=='volume' and value is None:
            values[field]=None
            continue
        try:
            if isinstance(value,bool):raise ValueError()
            number=float(value)
            if not math.isfinite(number):raise ValueError()
        except (TypeError,ValueError,OverflowError):
            raise DataError('Campo ausente ou numérico inválido: '+field,'invalid_history_field') from None
        values[field]=number
    epoch=values['epoch']
    if not epoch.is_integer() or epoch<=0 or epoch%60:
        raise DataError('Epoch inválido ou não alinhado a M1.','invalid_history_timestamp')
    if epoch>cursor:
        raise DataError('Candle posterior ao fim solicitado.','history_out_of_range')
    c=Candle(int(epoch),*[values[k] for k in ('open','high','low','close','volume')])
    if not valid_candle(c):
        raise DataError('OHLC inconsistente ou volume negativo.','invalid_history_ohlc')
    return c


async def download(assets,start,end):
    cfg=Config(assets=assets)
    provider=DerivProvider(cfg)
    data={};errors={}
    for asset in assets:
        print('Consultando histórico Deriv: '+asset,flush=True)
        cursor=end-1;found={};context={};page_number=0
        try:
            # 5000 candles per page; fetch at least 100 M5 bars for warm-up.
            while cursor>=start-30000:
                page_number+=1
                context={'page':page_number,'request_end':cursor,'accepted_candles':len(found)}
                response=await provider.request({'ticks_history':provider.symbol(asset),'end':cursor,
                    'style':'candles','granularity':60,'count':5000},'candles')
                raw=response.get('candles')
                if not isinstance(raw,list) or not raw:raise DataError('Histórico vazio.','empty_history')
                page=[]
                for index,row in enumerate(raw):
                    context['row_index']=index
                    # Only public candle fields are retained, never the full response.
                    context['candle']={k:str(row[k])[:100] for k in
                        ('epoch','open','high','low','close','volume') if k in row} if isinstance(row,dict) else {'type':type(row).__name__}
                    c=parse_history_candle(row,cursor)
                    if c.t+60<=end:page.append(c)
                context.pop('row_index',None)
                context.pop('candle',None)
                if not page:raise DataError('Histórico sem candles fechados.','empty_history')
                oldest=min(c.t for c in page)
                if oldest>cursor:raise DataError('Paginação sem progresso.','pagination')
                for c in page:
                    if c.t in found and found[c.t]!=c:raise DataError(f'Candles divergentes entre páginas no epoch {c.t}.','conflicting_history')
                    found[c.t]=c
                if oldest<=start-30000:break
                cursor=oldest-1
                await asyncio.sleep(.2)
            data[asset]=[c for _,c in sorted(found.items()) if c.t>=start-30000]
        except (DataError,KeyError,ValueError,TypeError,OverflowError) as error:
            errors[asset]={'code':getattr(error,'code','invalid_history'),
                           'detail':str(error) if isinstance(error,DataError) else 'Estrutura inválida: '+type(error).__name__,
                           'context':context}
            print(asset+': '+errors[asset]['code']+' — '+errors[asset]['detail'],flush=True)
            # Partial data is not silently labelled as full coverage.
            data[asset]=[]
    return data,errors


def events_for(data,key,interval,start,end):
    events=defaultdict(list);rejections=Counter();scans=0;valid_scans=0
    for asset,minutes in data.items():
        candles=aggregate(minutes,interval)
        for i,c in enumerate(candles):
            decision=c.t+interval+2
            if decision<start or decision>end:continue
            scans+=1
            # In production count=100 includes the open candle: 99 closed bars.
            snapshot=candles[max(0,i-98):i+1]
            try:closed=validate(snapshot,decision,max_age=interval+30,interval=interval)
            except DataError as error:
                rejections['dados: '+str(error)]+=1;continue
            valid_scans+=1
            candidate,reason=STRATEGIES[key](closed,strict=True) if key=='scan_all' else STRATEGIES[key](closed)
            if candidate and candidate.get('score',0)>=70:
                events[decision].append((asset,candidate))
            else:rejections[reason or 'Pontuação inferior a 70']+=1
    return sorted(events.items()),rejections,scans,valid_scans


def simulate(events,quotes,interval,duration,end,assets):
    """Settlements affect risk gates only once available at expiration +65s."""
    trades=[];seen=set();entries=[];settled=[];pending=[];blocks=Counter()
    asset_order={asset:i for i,asset in enumerate(assets)}
    for decision,candidates in events:
        ready=[trade for trade in pending if trade['expiry_at']+65<=decision]
        pending=[trade for trade in pending if trade['expiry_at']+65>decision]
        settled.extend(sorted((trade for trade in ready if trade['result'] in ('WIN','LOSS','EMPATE')),
                              key=lambda t:t['expiry_at']))
        entry=math.ceil((decision+60)/60)*60
        pause_until=settled[-1]['expiry_at']+65+900 if len(settled)>=2 and all(t['result']=='LOSS' for t in settled[-2:]) else 0
        if decision<pause_until:
            blocks['pausa_2_losses']+=len(candidates);continue
        entries=[value for value in entries if abs(value-entry)<3600]
        if len(entries)>=5:
            blocks['limite_5_hora']+=len(candidates);continue
        for asset,candidate in sorted(candidates,key=lambda item:asset_order[item[0]]):
            identity=(asset,candidate.get('strategy_resolved','selected'),candidate['direction'],candidate['setup_candle'])
            if identity in seen:blocks['duplicado']+=1;continue
            expiry=entry+duration
            prices=quotes[asset]
            opening=prices.get(entry);closing=prices.get(expiry)
            if expiry+60>end or opening is None or closing is None:
                outcome='INDISPONIVEL'
            elif opening==closing:outcome='EMPATE'
            else:outcome='WIN' if (closing>opening)==(candidate['direction']=='CALL') else 'LOSS'
            trade={'asset':asset,'direction':candidate['direction'],'created_at':decision,'entry_at':entry,
                   'expiry_at':expiry,'result':outcome,'score':candidate['score'],
                   'strategy_resolved':candidate.get('strategy_resolved','selected'),
                   'entry_price':opening,'expiry_price':closing}
            trades.append(trade);pending.append(trade);seen.add(identity);entries.append(entry)
            break
    return trades,dict(blocks)


def run(data,start,end,assets):
    groups=[];all_trades=[]
    quotes={asset:{c.t:c.o for c in rows} for asset,rows in data.items()}
    for key in STRATEGIES:
        print('Backtest: '+key,flush=True)
        for timeframe,interval in TIMEFRAMES.items():
            events,rejections,scans,valid_scans=events_for(data,key,interval,start,end)
            for duration in (60,120,180,300):
                trades,blocked=simulate(events,quotes,interval,duration,end,assets)
                totals=Counter(t['result'] for t in trades)
                evaluated=totals['WIN']+totals['LOSS']
                status='no_data' if not any(data.values()) else 'completed' if valid_scans else 'insufficient_history'
                if status=='completed' and key in ('vwap','sr_volume_breakout') and not any(
                    c.v is not None and c.v>0 for rows in data.values() for c in rows):status='volume_unavailable'
                row={'strategy':key,'timeframe':timeframe,'expiry_minutes':duration//60,'status':status,
                     'scans':scans,'valid_scans':valid_scans,'signals':len(trades),'wins':totals['WIN'],'losses':totals['LOSS'],
                     'ties':totals['EMPATE'],'unavailable':totals['INDISPONIVEL'],
                     'win_rate_pct':round(100*totals['WIN']/evaluated,2) if evaluated else None,
                     'evaluated':evaluated,'risk_blocks':blocked,'setup_rejections':dict(rejections)}
                groups.append(row)
                for t in trades:
                    all_trades.append({'strategy':key,'timeframe':timeframe,'expiry_minutes':duration//60,**t})
    return groups,all_trades


def write_report(output,report,trades):
    output.mkdir(parents=True,exist_ok=True)
    (output/'resultado.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    fields=['strategy','timeframe','expiry_minutes','status','scans','valid_scans','signals','wins','losses','ties','unavailable','evaluated','win_rate_pct']
    with (output/'estrategias.csv').open('w',newline='',encoding='utf-8-sig') as file:
        writer=csv.DictWriter(file,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(report['groups'])
    with (output/'operacoes.csv').open('w',newline='',encoding='utf-8-sig') as file:
        fields=['strategy','strategy_resolved','timeframe','expiry_minutes','asset','direction','entry_at','expiry_at','entry_price','expiry_price','result','score']
        writer=csv.DictWriter(file,fieldnames=fields,extrasaction='ignore');writer.writeheader()
        for trade in trades:writer.writerow({**trade,'entry_at':stamp(trade['entry_at']),'expiry_at':stamp(trade['expiry_at'])})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--days',type=int,default=7)
    parser.add_argument('--assets',nargs='+',default=['EUR/USD','GBP/USD','USD/JPY'])
    parser.add_argument('--output',type=Path,default=Path('backtest-resultados'))
    parser.add_argument('--input',type=Path,help='Arquivo historico.json gerado por uma execução anterior; procedência declarada, não certificada.')
    args=parser.parse_args()
    if not 1<=args.days<=60:parser.error('Use --days entre 1 e 60.')
    Config(assets=args.assets).validate()
    end=int(time.time()//60)*60;start=end-args.days*86400
    if args.input:
        raw=json.loads(args.input.read_text(encoding='utf8'));start=raw['start'];end=raw['end']
        data={asset:[Candle(**row) for row in raw.get('data',{}).get(asset,[])] for asset in args.assets}
        for asset,rows in data.items():
            if any(not valid_candle(c) or c.t%60 or c.t+60>end for c in rows):raise ValueError('Histórico importado inválido: '+asset)
            if any(b.t<=a.t for a,b in zip(rows,rows[1:])):raise ValueError('Histórico importado fora de ordem.')
        errors=raw.get('errors',{});origin='Arquivo local; procedência não verificada nesta execução'
    else:
        data,errors=asyncio.run(download(args.assets,start,end));origin='Deriv public WebSocket'
    args.output.mkdir(parents=True,exist_ok=True)
    archive={'source':origin,'start':start,'end':end,'data':{a:[c.json() for c in rows] for a,rows in data.items()},'errors':errors}
    payload=json.dumps(archive,ensure_ascii=False,sort_keys=True).encode('utf8')
    (args.output/'historico.json').write_bytes(payload)
    groups,trades=run(data,start,end,args.assets)
    report={'version':VERSION,'code_version':VERSION,'code_sha256':code_fingerprint(),
            'rules_version':RULES_VERSION,'pipeline_version':PIPELINE_VERSION,'selector_version':SELECTOR_VERSION,
            'method_version':'legacy-ohlc-replay-1.26.2','operational_equivalence':False,
            'method':{'operational_indicator_window':99,'decision_latency_seconds':2,'min_lead_seconds':60,
                'entry_rounding':'ceil_M1','hourly_limit':5,'signals_per_cycle':1,
                'entry_revalidation':False,'movement_dedup':'legacy_setup_candle_only','telegram_latency':False,
                'learning':False,'broker_confirmation':False},'status':'completed' if any(g['valid_scans'] for g in groups) else 'not_executed',
            'source':origin,'history_sha256':hashlib.sha256(payload).hexdigest(),'start':start,'end':end,
            'assets':args.assets,'download_errors':errors,'groups':groups}
    write_report(args.output,report,trades)
    print('Resultado: '+report['status']+' — '+str(args.output/'resultado.json'),flush=True)
    return 0 if report['status']=='completed' else 2

if __name__=='__main__':raise SystemExit(main())
