import time
from fastapi.testclient import TestClient
from app.main import create_app
from app.config import Config
from app.market import Candle
from tests.test_system import fixture,TG


def test_analysis_endpoint_engines_and_history_survive_restart(tmp_path):
    candles=fixture();shift=int(time.time()//300)*300-300-candles[-1].t
    candles=[Candle(c.t+shift,c.o,c.h,c.l,c.c,c.v) for c in candles]
    class Source:
        name='Fonte simulada de teste';ready=True;identity='test'
        async def candles(self,*args):return candles
        async def check_asset(self,*args):pass
        async def supports(self,*args):pass
    cfg=Config(database=str(tmp_path/'db'),assets=['EUR/USD'])
    with TestClient(create_app(cfg,Source(),TG(),clock=lambda:candles[-1].t+305)) as client:
        assert client.post('/api/scan/start',json={'provider':'deriv','assets':['EUR/USD'],'timeframe':'M5','duration_seconds':300}).status_code==202
        for _ in range(100):
            state=client.get('/api/state').json()
            if state['signals']:break
            time.sleep(.01)
        assert state['strategy']=='scan_all' and state['scan']['active']
        assert state['signals'],state['analyses']
        sid=state['signals'][0]['id']
        original=client.get('/api/signals/'+sid).json()
        assert original['signal']['evidence']['candles']
        assert client.post('/api/scan/stop').status_code==200
    with TestClient(create_app(cfg,Source(),TG(),clock=lambda:candles[-1].t+305)) as client:
        recovered=client.get('/api/signals/'+sid).json()
        assert recovered['signal']['evidence']==original['signal']['evidence']
        assert recovered['signal']['entry_at']==original['signal']['entry_at']
        assert recovered['events']
        assert not client.get('/api/state').json()['scan']['active']
