import asyncio
from unittest.mock import patch
from app.night_study import Study,current_batch,run_continuous


def test_batches_resume_and_never_duplicate_setup_across_batches(tmp_path):
    path=tmp_path/'study.db'
    study=Study(path)
    first=current_batch(study)
    for i in range(100):
        assert study.add(first,'EUR/USD','cci',{'direction':'CALL','score':85,'setup_candle':300+i*300},[],302+i*300)
    second=current_batch(study)
    assert second!=first
    assert not study.add(second,'EUR/USD','cci',{'direction':'CALL','score':85,'setup_candle':300},[],302)
    assert study.add(second,'EUR/USD','cci',{'direction':'CALL','score':85,'setup_candle':30300},[],30302)
    study.db.close()
    study=Study(path)
    assert current_batch(study)==second and study.count(second)==1
    study.db.close()


def test_worker_runs_without_night_window_and_stops_cleanly(tmp_path):
    async def run():
        study=Study(tmp_path/'study.db');status={};calls=[]
        async def sleep(_):
            if calls:raise asyncio.CancelledError
        async def cycle(*args):calls.append(args)
        with patch('app.night_study.asyncio.sleep',sleep),patch('app.night_study.cycle',cycle),patch.object(study,'export'):
            try:await run_continuous(study,object(),lambda:['EUR/USD'],tmp_path,status)
            except asyncio.CancelledError:pass
        assert len(calls)==1 and calls[0][2]==['EUR/USD'] and calls[0][4]==float('inf')
        assert status['observations']==0 and not status['active']
        study.db.close()
    asyncio.run(run())
