"""Read-only public Deriv probe. Never authenticates accounts or sends messages."""
import asyncio
import json
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.config import Config
from app.deriv import DerivProvider
from app.market import DataError,validate
from app.timeframes import TIMEFRAMES,max_age

async def main():
    provider=DerivProvider(Config())
    report={'checked_at_utc':time.time(),'source':'Deriv public','live':True,'steps':[]}
    try:
        await provider.check_asset('EUR/USD')
        report['steps'].append({'step':'active_symbols','status':'ok'})
        for timeframe,interval in TIMEFRAMES.items():
            rows=await provider.candles('EUR/USD',interval)
            closed=validate(rows,time.time(),max_age=max_age(timeframe,provider.config),interval=interval)
            report['steps'].append({'step':'candles_'+timeframe,'status':'ok','closed_count':len(closed),'last_open_utc':closed[-1].t,'real_volume_available':all(c.v is not None and c.v>0 for c in closed[-21:])})
        for direction in ('CALL','PUT'):
            for duration in (60,120,180,300):
                await provider.supports('EUR/USD',direction,duration)
                report['steps'].append({'step':f'contract_{direction}_{duration//60}min','status':'ok'})
        report['status']='verified_read_only'
    except DataError as error:
        report.update(status='not_verified',error_code=error.code,detail=str(error))
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':asyncio.run(main())
