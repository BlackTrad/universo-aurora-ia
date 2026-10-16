import asyncio
import json
import httpx
from app.config import Config
from app.market import Candle
from app.store import Store
from app.telegram import Telegram
from tests.test_system import setup,drain

def test_totals_exclude_unresolved_cancelled_and_ties_from_rate():
    store=Store(':memory:')
    for i,(out,state,pending) in enumerate([('WIN','encerrado',False),('LOSS','encerrado',False),('EMPATE','encerrado',False),('WIN','cancelado',False),('WIN','encerrado',True)]):
        store.add_signal({'id':str(i),'created_at':i,'result':out,'state':state,'settlement_pending':pending},str(i))
    assert store.result_totals()['wins']==1
    assert store.result_totals()['losses']==1
    assert store.result_totals()['ties']==1
    assert store.result_totals()['win_rate_pct']==50
    store.db.close()

def test_settlement_sends_result_once_with_totals(tmp_path):
    async def run():
        service,now=setup(tmp_path)
        service.start(['EUR/USD'],60);await drain(service)
        signal=service.store.signals()[0]
        now[0]=signal['expiry_at']+65
        service.provider.settlement=[Candle(signal['entry_at'],100,102,99,101),Candle(signal['expiry_at'],101,102,100,101)]
        sent=[]
        def response(request):
            sent.append(json.loads(request.content));return httpx.Response(200,json={'ok':True,'result':{'message_id':456}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            service.telegram=Telegram(Config(telegram_token='123:test',telegram_chat='1'),client)
            await service.settle(signal);await drain(service)
            await asyncio.gather(service.deliver_result(signal['id']),service.deliver_result(signal['id']))
            await service.tick();await drain(service)
        assert len(sent)==1
        assert 'WIN' in sent[0]['text'] and 'Vitórias: 1' in sent[0]['text'] and '100,00%' in sent[0]['text']
        assert service.store.signal(signal['id'])['telegram_result']['status']=='enviado'
        service.store.db.close()
    asyncio.run(run())

def test_restart_does_not_resend_uncertain_result():
    store=Store(':memory:')
    store.add_signal({'id':'s','created_at':1,'state':'encerrado','settlement_pending':False,'telegram_result':{'status':'enviando'}},'s')
    store.recover(100)
    assert store.signal('s')['telegram_result']['status']=='incerto'
    assert not store.pending_result_messages()
    store.db.close()
