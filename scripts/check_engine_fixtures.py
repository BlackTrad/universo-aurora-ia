"""Deterministic technical comparison only; not an accuracy or financial backtest."""
import hashlib
import json
from pathlib import Path
import random
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.market import Candle
from app.strategies import STRATEGIES


def report():
    cases=[]
    root=Path(__file__).resolve().parents[1]
    for name in ('call_fixture.json','breakout_only_fixture.json'):
        rows=[Candle(**r) for r in json.loads((root/'tests'/name).read_text())]
        cases.append((name,rows))
    for seed in (0,42,1085,21885):
        rng=random.Random(seed);rows=[];price=1.1
        for i in range(80):
            change=rng.uniform(-.0003,.0003);closing=price+change
            rows.append(Candle(1800000000+i*300,price,max(price,closing)+rng.uniform(.0001,.0003),
                               min(price,closing)-rng.uniform(.0001,.0003),closing))
            price=closing
        cases.append(('seed-'+str(seed),rows))
    output=[]
    for name,rows in cases:
        for length in range(40,len(rows)+1):
            for key,evaluate in STRATEGIES.items():
                output.append([name,length,key,evaluate(rows[:length])])
    encoded=json.dumps(output,sort_keys=True,separators=(',',':'),ensure_ascii=False)
    return {'mode':'synthetic_technical_comparison','evaluations':len(output),
            'registry':list(STRATEGIES),'sha256':hashlib.sha256(encoded.encode()).hexdigest(),
            'note':'Prefixes contain only candles available at each decision. Equality is not performance validation.'}


if __name__=='__main__':print(json.dumps(report(),indent=2,ensure_ascii=False))
