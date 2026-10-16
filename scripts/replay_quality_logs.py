"""Audit selection on already-published signals; NOT a full/out-of-sample backtest.

No network, orders, Telegram or parameter optimization. Revalidation is excluded:
logs may not contain the required pre-entry M1 candles. This replay cannot infer
new signals, quota effects or outcomes of previously rejected opportunities.
"""
import argparse
from collections import Counter
from datetime import datetime
import json
import logging
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.market import Candle, validate
from app.strategies import scan_all
from app.timeframes import TIMEFRAMES

def replay(paths):
    rows=[]
    for path in paths:
        for line in Path(path).read_text(encoding='utf8').splitlines():
            try: row=json.loads(line)
            except (ValueError,TypeError): continue
            if row.get('event') in ('candles.received','setup.evaluated','signal.created','settlement.finished'):
                rows.append(row)
    rows.sort(key=lambda row:row['at_utc'])
    batches={};setups={};signals={};outcomes={}
    for row in rows:
        key=(row.get('analysis_id'),row.get('asset'))
        if row['event']=='candles.received':batches[key]=row
        elif row['event']=='setup.evaluated':setups[key]=(row,batches.get(key))
        elif row['event']=='signal.created':signals[row['signal_id']]=(row,setups.get(key))
        elif row['event']=='settlement.finished' and row.get('result') in ('WIN','LOSS','EMPATE'):
            outcomes[row['signal_id']]=row['result']
    totals=Counter();kept=Counter();rejections=Counter();details=[]
    for sid,(signal,setup) in signals.items():
        if not setup or not setup[1] or sid not in outcomes:continue
        evaluation,batch=setup
        if not evaluation.get('candidate'):continue
        interval=TIMEFRAMES[signal.get('timeframe','M5')]
        now=datetime.fromisoformat(evaluation['at_utc']).timestamp()
        candles=validate([Candle(**c) for c in batch['candles']],now,interval+30,interval)
        selected,reason=scan_all(candles,strict=True)
        # Same direction and the original entry/expiry are required for comparison.
        accepted=bool(selected and selected['direction']==signal['direction'])
        result=outcomes[sid];totals[result]+=1
        if accepted:kept[result]+=1
        else:rejections[reason or 'Direction changed']+=1
        details.append({'asset':signal['asset'],'entry_at':signal['entry_at'],'result':result,
                        'kept':accepted,'reason':reason,'quality':selected.get('quality') if selected else None})
    def stats(values):
        n=values['WIN']+values['LOSS']
        return {'signals':sum(values.values()),'wins':values['WIN'],'losses':values['LOSS'],
                'ties':values['EMPATE'],'win_rate_pct':round(100*values['WIN']/n,2) if n else None}
    return {'scope':'Replay retrospectivo dos sinais emitidos; sem revalidação, aprendizado ou simulação de limites.',
            'baseline':stats(totals),'selection_only':stats(kept),'rejected':dict(rejections),
            'limitations':['Amostra usada na auditoria, sem validação independente.',
                            'Não comprova rentabilidade ou probabilidade futura.',
                            'Não estima sinais novos nem resultado da revalidação M1.'], 'signals':details}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('logs',nargs='+',type=Path)
    args=parser.parse_args()
    logging.disable(logging.CRITICAL)
    print(json.dumps(replay(args.logs),ensure_ascii=False,indent=2))
