"""Chronological comparison, isolated from production, Telegram and learning."""
import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
from functools import lru_cache

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.experiments import KEYS, RULES, VERSION, evaluate, rule_snapshot
from app.market import Candle, DataError, valid_candle, validate
from app.strategies import scan_all
from app.strategies.helpers import atr
from app.strategies.regime import classify
from app.strategies.quality import entry_guard
from scripts.backtest import aggregate
from app.research_filters import FILTERS, PARAMETERS, apply_filter


def digest_code():
    paths=sorted((ROOT/'app').rglob('*.py'))+[ROOT/'scripts/backtest.py',Path(__file__).resolve()]
    h=hashlib.sha256()
    for path in paths:
        h.update(str(path.relative_to(ROOT)).encode());h.update(path.read_bytes())
    return h.hexdigest()


def load_history(path):
    blob=path.read_bytes();raw=json.loads(blob)
    start,end=raw['start'],raw['end']
    if (type(start) is not int or type(end) is not int or start<=0 or start%60 or end%60 or end-start<3600):
        raise ValueError('Período deve ter ao menos 60 minutos e limites UTC alinhados a M1.')
    source=raw.get('source_identity') or raw.get('source')
    if not isinstance(source,str) or not source.strip():raise ValueError('Declare a fonte e sua procedência no histórico.')
    data={}
    for asset,values in raw.get('data',{}).items():
        rows=[Candle(**row) for row in values]
        if any(not valid_candle(c) or type(c.t) is not int or c.t<=0 or c.t%60 or c.t+60>end for c in rows):
            raise ValueError('OHLC/timestamp inválido: '+asset)
        if any(b.t<=a.t for a,b in zip(rows,rows[1:])):raise ValueError('Duplicidade ou ordem inválida: '+asset)
        data[asset]=rows
    if not data or not any(data.values()):raise ValueError('Histórico vazio; não há comparação de desempenho.')
    return data,raw,hashlib.sha256(blob).hexdigest(),source


def partition(start,end):
    first=(start+(end-start)*3//5)//60*60
    second=(start+(end-start)*4//5)//60*60
    return {'development':[start,first],'validation':[first,second],'test':[second,end]}


def wilson(wins,total):
    if not total:return None
    z=1.959963984540054;p=wins/total;den=1+z*z/total
    center=(p+z*z/(2*total))/den
    radius=z*math.sqrt(p*(1-p)/total+z*z/(4*total*total))/den
    return [round(100*(center-radius),2),round(100*(center+radius),2)]


def non_overlapping(trades):
    selected=[];last={}
    for t in sorted(trades,key=lambda t:(t['entry_at'],t['created_at'],t['asset'])):
        if t['result'] not in ('WIN','LOSS','EMPATE'):continue
        key=(t.get('source_identity','declared_source'),t['asset'])
        if t['entry_at']<last.get(key,0):continue
        selected.append(t);last[key]=t['expiry_at']+60
    return selected


@lru_cache(maxsize=256)
def hour_exposure(start,end):
    counts=Counter();cursor=start
    while cursor<end:
        stop=min(end,(int(cursor//60)+1)*60)
        hour=datetime.fromtimestamp(cursor,ZoneInfo('America/Sao_Paulo')).hour
        counts[hour]+=stop-cursor;cursor=stop
    return counts


def metrics(trades,seconds):
    totals=Counter(t['result'] for t in trades);n=totals['WIN']+totals['LOSS']
    streak=maximum=0;balance=peak=drawdown=0
    for trade in sorted(trades,key=lambda t:(t['settled_at'],t['created_at'],t['asset'])):
        if trade['result'] not in ('WIN','LOSS','EMPATE'):continue
        streak=streak+1 if trade['result']=='LOSS' else 0;maximum=max(maximum,streak)
        balance+=1 if trade['result']=='WIN' else -1 if trade['result']=='LOSS' else 0
        peak=max(peak,balance);drawdown=max(drawdown,peak-balance)
    primary=non_overlapping(trades);counts=Counter(t['result'] for t in primary);pn=counts['WIN']+counts['LOSS']
    known=[t for t in trades if t['result'] in ('WIN','LOSS','EMPATE') and t.get('payout_pct') is not None]
    expectancy=(sum(t['payout_pct']/100 if t['result']=='WIN' else -1 if t['result']=='LOSS' else 0 for t in known)/len(known)) if known else None
    return {'published':len(trades),'wins':totals['WIN'],'losses':totals['LOSS'],'ties':totals['EMPATE'],
            'cancelled':totals['CANCELADO'],'unavailable':totals['INDISPONIVEL'],'evaluated':n,
            'win_rate_pct':round(100*totals['WIN']/n,2) if n else None,'wilson_95_pct':wilson(totals['WIN'],n),
            'max_loss_streak':maximum,'max_drawdown_equal_units':drawdown,
            'signals_per_hour':round(len(trades)*3600/seconds,4),
            'expectancy_units':round(expectancy,6) if expectancy is not None else None,
            'payout_known_operations':len(known),'payout_kind':'declarado; estimativa indicativa' if known else 'indisponível',
            'non_overlapping':{'wins':counts['WIN'],'losses':counts['LOSS'],'ties':counts['EMPATE'],'evaluated':pn,
                'win_rate_pct':round(100*counts['WIN']/pn,2) if pn else None,'wilson_95_pct':wilson(counts['WIN'],pn),
                'excluded_overlaps':n+totals['EMPATE']-len(primary)},
            'sample_status':'insufficient' if n<100 else 'descriptive_only'}


def replay(data, *, key, bounds, interval, duration, hourly_limit=10, simultaneous_limit=2,filter_name=None,payout_pct=None):
    """Same guard and indicative quotes; ideal scheduling, no network simulation.

    Each fold has independent risk state. Prior candles may warm indicators.
    Labels/revalidation from outside the fold cannot affect its results.
    """
    start,end=bounds
    visible={asset:[c for c in rows if c.t+60<=end] for asset,rows in data.items()}
    quotes={a:{c.t:c.o for c in rows} for a,rows in visible.items()}
    indexes={a:[c.t for c in rows] for a,rows in visible.items()}
    higher={a:aggregate(rows,interval*5) for a,rows in visible.items()} if filter_name=='htf_alignment' else {}
    events=defaultdict(list);diagnostics=[];blocks=Counter();valid_scans=0
    for asset,minutes in visible.items():
        bars=aggregate(minutes,interval)
        for i,c in enumerate(bars):
            now=c.t+interval+2
            if not start<=now<end:continue
            snapshot=bars[max(0,i-98):i+1]
            entry=math.ceil((now+60)/60)*60;expiry=entry+duration
            base={'asset':asset,'strategy':key,'decision_at':now,'entry_at':entry,'expiry_at':expiry}
            if expiry+65>=end:
                blocks['purged_boundary']+=1;diagnostics.append({**base,'accepted':False,'reason':'Resultado cruzaria a fronteira cronológica.'});continue
            try:closed=validate(snapshot,now,max_age=interval+30,interval=interval)
            except DataError as error:
                blocks['invalid_data']+=1;diagnostics.append({**base,'accepted':False,'reason':str(error)});continue
            valid_scans+=1
            if key=='baseline_scan_all':
                candidate,reason=scan_all(closed,strict=True)
                audit={'accepted':bool(candidate),'reason':reason,'regime':classify(closed) or 'indefinido'}
                if candidate:
                    audit.update(candidate,atr=atr(closed),version='1.22.10')
            else:
                audit=evaluate(key,closed,now=now,interval=interval)
            if filter_name and audit['accepted']:
                # Only higher bars already closed at this decision may participate.
                htf=[c for c in higher.get(asset,[]) if c.t+interval*5<=now][-99:]
                checked=apply_filter(filter_name,closed,audit,now=now,interval=interval,higher=htf)
                audit={**audit,'filters':[checked],'accepted':checked['passed'],
                       'reason':audit['reason'] if checked['passed'] else checked['detail']}
            if audit['accepted'] and audit.get('entry_policy')=='next_analysis_candle':
                entry=audit['target_entry_at'];expiry=entry+duration
                base.update(entry_at=entry,expiry_at=expiry)
                timing={'policy':'next_analysis_candle','target_entry_at':entry,
                        'decision_at':now,'lead_seconds':entry-now,
                        'latency_seconds':max(0,now-entry),'minimum_lead_seconds':60,
                        'actionable':now<=entry-60}
                audit['entry_timing']=timing
                if not timing['actionable']:
                    blocks['insufficient_lead']+=1
                    diagnostics.append({**base,**audit,'state':'cancelled','result':'CANCELADO',
                        'cancellation_reason':'Próxima abertura sem antecedência após fechamento; sem remarcar entrada.',
                        'snapshot_first_at':closed[0].t,'snapshot_last_at':closed[-1].t,
                        'candles':[c.json() for c in closed]})
                    continue
            diagnostics.append({**base,**audit,'snapshot_first_at':closed[0].t,
                                'snapshot_last_at':closed[-1].t,
                                **({'candles':[c.json() for c in closed]} if audit['accepted'] else {})})
            if not audit['accepted']:
                blocks['no_setup']+=1;continue
            events[now].append((asset,audit,closed))
    trades=[];seen=set();active=[];pause_until=0;loss_streak=0
    for now,candidates in sorted(events.items()):
        # A cancellation or settlement only becomes known at its scheduled time.
        ready=sorted((t for t in active if t['released_at']<=now),key=lambda t:(t['released_at'],t['created_at'],t['asset']))
        active=[t for t in active if t['released_at']>now]
        for t in ready:
            if t['result'] not in ('WIN','LOSS','EMPATE'):continue
            loss_streak=loss_streak+1 if t['result']=='LOSS' else 0
            if loss_streak>=2:pause_until=max(pause_until,t['settled_at']+900)
        for asset,c,closed in candidates:
            identity=(asset,c.get('strategy_resolved',key),c['direction'],c.get('setup_identity',c['setup_candle']))
            entry=c['target_entry_at'] if c.get('entry_policy')=='next_analysis_candle' else math.ceil((now+60)/60)*60
            expiry=entry+duration
            if identity in seen:blocks['duplicate']+=1;continue
            if now<pause_until:blocks['loss_pause']+=1;continue
            if len(active)>=simultaneous_limit:blocks['simultaneous_limit']+=1;continue
            if sum(abs(t['entry_at']-entry)<3600 for t in trades)>=hourly_limit:blocks['hourly_limit']+=1;continue
            if c.get('valid_until',entry)<entry:blocks['expired_setup']+=1;continue
            # Snapshot at entry-45: never use a candle that closes after checking.
            check_at=entry-45;stop=bisect_right(indexes[asset],int(check_at//60)*60-60)
            recent=visible[asset][max(0,stop-5):stop]
            signal={'entry_at':entry,'direction':c['direction'],
                    'evidence':{'candles':[closed[-1].json()]},'entry_validation':{'atr':c['atr']}}
            validation=None;reason=''
            try:validation,reason=entry_guard(signal,recent,check_at)
            except DataError as error:reason=str(error)
            # Experimental geometry must also remain intact on closed M1 data.
            if not reason and key!='baseline_scan_all':
                limit=c['invalidation'][0]
                if c.get('invalidation_line'):
                    from app.third_touch import invalidation_limit
                    limit=invalidation_limit(c['invalidation_line'],recent[-1].t)
                invalid=recent[-1].c<limit if c['direction']=='CALL' else recent[-1].c>limit
                if invalid:
                    reason='Invalidação técnica antes da entrada.'
            opening,closing=quotes[asset].get(entry),quotes[asset].get(expiry)
            if reason:outcome='CANCELADO'
            elif opening is None or closing is None:outcome='INDISPONIVEL'
            elif opening==closing:outcome='EMPATE'
            else:outcome='WIN' if (closing>opening)==(c['direction']=='CALL') else 'LOSS'
            release=check_at if outcome=='CANCELADO' else expiry+65
            trade={'asset':asset,'strategy':key,'resolved':c.get('strategy_resolved',key),'version':c.get('version','1.22.10'),
                   'direction':c['direction'],'regime':c['regime'],'score':c['score'],'setup_candle':c['setup_candle'],
                   'created_at':now,'entry_at':entry,'expiry_at':expiry,'settled_at':release,'released_at':release,
                   'entry_price':None if outcome=='CANCELADO' else opening,
                   'expiry_price':None if outcome=='CANCELADO' else closing,
                   'result':outcome,'entry_validation':validation,'cancellation_reason':reason,
                   'interval_seconds':interval,'duration_seconds':duration,'result_kind':'simulado_indicativo',
                   'payout_pct':payout_pct,'variant':filter_name or 'base'}
            trades.append(trade);active.append(trade);seen.add(identity)
            # A published cancellation still consumes the rolling signal quota.
    groups=[]
    for asset in data:
        subset=[t for t in trades if t['asset']==asset]
        groups.append({'asset':asset,'regime':'all',**metrics(subset,end-start)})
        for regime in ('tendencial','lateral','indefinido'):
            groups.append({'asset':asset,'regime':regime,**metrics([t for t in subset if t['regime']==regime],end-start)})
    exposure=hour_exposure(start,end)
    by_hour=[{'hour_brasilia':h,'exposure_seconds':exposure[h],**metrics([t for t in trades if datetime.fromtimestamp(t['created_at'],ZoneInfo('America/Sao_Paulo')).hour==h],max(1,exposure[h]))}
             for h in sorted({datetime.fromtimestamp(t['created_at'],ZoneInfo('America/Sao_Paulo')).hour for t in trades})]
    return {'strategy':key,'variant':filter_name or 'base','bounds':bounds,'valid_scans':valid_scans,'blocks':dict(blocks),
            'interval_seconds':interval,'duration_seconds':duration,'by_hour_brasilia':by_hour,
            **metrics(trades,end-start),'by_asset_regime':groups},trades,diagnostics


def write_new(path,value):
    with path.open('x',encoding='utf8') as f:json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True,help='historico.json com OHLC M1 e fonte declarada')
    parser.add_argument('--output',type=Path,required=True,help='Diretório novo de resultados')
    parser.add_argument('--timeframe',choices=['M1','M5'],default='M1')
    parser.add_argument('--expiry-minutes',type=int,choices=[1,2,3,5],default=1)
    parser.add_argument('--hourly-limit',type=int,default=10)
    parser.add_argument('--simultaneous-limit',type=int,choices=[1,2],default=2)
    parser.add_argument('--holdout-plan',type=Path,help='Avaliar somente teste final com plano previamente congelado')
    parser.add_argument('--investigate-filters',nargs='*',choices=FILTERS,default=[],help='Comparar cada filtro isoladamente; não combina automaticamente')
    parser.add_argument('--matrix',action='store_true',help='M1/M5 × expirações 1/2/3/5, sempre separados')
    parser.add_argument('--payout-pct',type=float,help='Payout constante explicitamente informado; estimativa, não payout histórico certificado')
    parser.add_argument('--trial-log',type=Path,help='Registro cumulativo de configurações; padrão research-trials.jsonl ao lado das saídas')
    args=parser.parse_args()
    if not 1<=args.hourly_limit<=60:parser.error('Limite horário deve estar entre 1 e 60.')
    if args.payout_pct is not None and (not math.isfinite(args.payout_pct) or not 0<args.payout_pct<=100):parser.error('Payout declarado deve estar entre 0 e 100%.')
    if len(set(args.investigate_filters))!=len(args.investigate_filters):parser.error('Não repita filtros.')
    data,raw,digest,source=load_history(args.input)
    interval=60 if args.timeframe=='M1' else 300;duration=args.expiry_minutes*60
    combinations=[(i,d) for i in (60,300) for d in (60,120,180,300)] if args.matrix else [(interval,duration)]
    scenarios=[(key,None) for key in ['baseline_scan_all',*KEYS]]+[(key,name) for key in KEYS for name in args.investigate_filters]
    plan={'experiment_version':VERSION,'code_sha256':digest_code(),'history_sha256':digest,
          'source_declared':source,'provenance_verified':False,'bounds':partition(raw['start'],raw['end']),
          'interval':interval,'duration':duration,'hourly_limit':args.hourly_limit,
          'simultaneous_limit':args.simultaneous_limit,'rules':asdict(RULES),
          'strategy_rules':{key:rule_snapshot(key) for key in KEYS},
          'combinations':combinations,'scenarios':scenarios,'filter_parameters':PARAMETERS,'payout_pct':args.payout_pct,
          'strategies':['baseline_scan_all',*KEYS],'production_enabled':False,
          'limitations':['OHLC e resultados indicativos, sem contratos executados.',
             'Agenda ideal em fechamento+2s; sem latência, custo ou falhas de rede/Telegram.',
             'Memória operacional desativada em todos os cenários; dados simulados nunca a alimentam.',
             'Regime é o classificador EMA/ATR existente; limiares não calibrados.',
             'Wilson é descritivo; consulte o subconjunto sem sobreposição. Dependência serial pode permanecer.',
             'Drawdown em unidades ±1 é diagnóstico, sem payout ou lucro monetário.',
             'Resultado indisponível libera vaga em expiração+65; produção pode esperar mais.',
             'Sem reconstrução do status de mercado e de consultas reais; comparação não é produção integral.']}
    folds=['development','validation']
    if args.holdout_plan:
        frozen=json.loads(args.holdout_plan.read_text(encoding='utf8'))
        if frozen!=json.loads(json.dumps(plan)):parser.error('Plano congelado diverge do código, dados ou parâmetros; teste final recusado.')
        folds=['test']
    args.output.mkdir(parents=True,exist_ok=False)
    write_new(args.output/'plan.json',plan)
    trial=args.trial_log or args.output.parent/'research-trials.jsonl'
    trial.parent.mkdir(parents=True,exist_ok=True)
    with trial.open('a',encoding='utf8') as log:
        log.write(json.dumps({'plan':plan,'evaluated_folds':folds,'output':str(args.output.resolve())},ensure_ascii=False)+'\n')
    summaries=[];all_trades=[]
    with (args.output/'diagnostics.jsonl').open('x',encoding='utf8') as diagnostics:
        for fold in folds:
            for interval,duration in combinations:
                for key,name in scenarios:
                    summary,trades,audit=replay(data,key=key,bounds=plan['bounds'][fold],interval=interval,duration=duration,
                        hourly_limit=args.hourly_limit,simultaneous_limit=args.simultaneous_limit,filter_name=name,payout_pct=args.payout_pct)
                    summaries.append({'fold':fold,**summary})
                    all_trades.extend({'fold':fold,**trade} for trade in trades)
                    for row in audit:diagnostics.write(json.dumps({'fold':fold,'variant':name or 'base',
                        'interval_seconds':interval,'duration_seconds':duration,**row},ensure_ascii=False,allow_nan=False)+'\n')
    lookup={(g['fold'],g['strategy'],g['interval_seconds'],g['duration_seconds'],g['variant']):g for g in summaries}
    deltas=[]
    for g in summaries:
        if g['variant']=='base':continue
        base=lookup[(g['fold'],g['strategy'],g['interval_seconds'],g['duration_seconds'],'base')]
        deltas.append({'fold':g['fold'],'strategy':g['strategy'],'variant':g['variant'],
            'interval_seconds':g['interval_seconds'],'duration_seconds':g['duration_seconds'],
            'signal_count_delta':g['published']-base['published'],
            'win_rate_delta_points':round(g['win_rate_pct']-base['win_rate_pct'],2)
                if g['win_rate_pct'] is not None and base['win_rate_pct'] is not None else None,
            'note':'Comparação descritiva de um filtro; não é evidência independente de causalidade ou rentabilidade.'})
    report={'plan':plan,'evaluated_folds':folds,'final_test_status':'evaluated' if args.holdout_plan else 'sealed',
            'promotion_status':'not_validated_disabled','groups':summaries,'single_filter_deltas':deltas}
    write_new(args.output/'results.json',report);write_new(args.output/'trades.json',all_trades)
    print('Comparação concluída; teste final '+report['final_test_status']+'. '+str(args.output/'results.json'))


if __name__=='__main__':main()
