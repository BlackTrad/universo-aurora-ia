import asyncio
from fastapi.testclient import TestClient
from app.main import create_app
from app.config import Config
from app.store import Store
from tests.test_system import setup,drain,Feed,TG

def test_limits_saved_validated_and_preserved(tmp_path):
    cfg=Config(database=str(tmp_path/'db'))
    body={'provider':'deriv','assets':['EUR/USD'],'hourly_limit':7,'simultaneous_limit':1,'minimum_score':92,'loss_pause_after':3,'pause_minutes':20}
    with TestClient(create_app(cfg,Feed(),TG())) as client:
        assert client.put('/api/settings',json=body).status_code==200
        assert client.put('/api/settings',json={'provider':'deriv','assets':['EUR/USD']}).json()['hourly_limit']==7
        for key,value in [('hourly_limit',0),('hourly_limit',1.5),('hourly_limit',True),('minimum_score',69),('simultaneous_limit',3),('pause_minutes',0)]:
            assert client.put('/api/settings',json={**body,key:value}).status_code==422
    with TestClient(create_app(cfg,Feed(),TG())) as client:
        settings=client.get('/api/state').json()['settings']
        for key in ['hourly_limit','simultaneous_limit','minimum_score','loss_pause_after','pause_minutes']:assert settings[key]==body[key]

def test_quality_and_simultaneous_limits_reach_motor(tmp_path):
    async def run():
        service,_=setup(tmp_path)
        service.store.save_settings({'minimum_score':100,'simultaneous_limit':1})
        service.start(['EUR/USD','GBP/USD'],300);await drain(service)
        assert not service.store.signals()
        service.store.save_settings({'minimum_score':70,'simultaneous_limit':1})
        service.start(['EUR/USD','GBP/USD'],300);await drain(service)
        assert len(service.store.signals())==1
        service.start(['GBP/USD'],300);await drain(service)
        assert len(service.store.signals())==1
    asyncio.run(run())

def test_custom_hourly_limit_and_pause():
    store=Store(':memory:');store.save_settings({'hourly_limit':3,'loss_pause_after':3,'pause_minutes':20})
    for i in range(3):
        s={'id':str(i),'created_at':1000+i,'entry_at':1100+i,'expiry_at':1160+i,'state':'encerrado','result':'LOSS','settled_at':1200+i}
        assert store.add_apex_signal(s,str(i))[0]
    limits=store.apex_limits(1210)
    assert limits['remaining']==0 and limits['pause_until']==2402
    assert not store.add_apex_signal({'id':'extra','created_at':1210,'entry_at':1300},'extra')[0]
    store.db.close()
