import asyncio
import json
import time
from unittest.mock import patch
import httpx
import pytest
from app.config import Config
from app.telegram import Telegram, caption, compact_complete_text, text_units


def signal():
    return {'id':'internal-id','asset':'EUR/USD','direction':'CALL','entry_at':time.time()+120,
            'expiry_at':time.time()+180,'duration_seconds':60,'timeframe':'M1','score':85,
            'reasons':['rejeição do suporte'],'evidence':{'pattern':'engolfo de alta'}}


def test_compact_signal_uses_only_engine_and_telegram_photo():
    async def run():
        requests=[]
        def handle(request):
            requests.append(request)
            assert request.url.host=='api.telegram.org' and request.url.path.endswith('/sendPhoto')
            assert b'OPORTUNIDADE ENCONTRADA' not in request.content
            assert b'\x89PNG\r\n\x1a\n' in request.content
            return httpx.Response(200,json={'ok':True,'result':{'message_id':1}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            tg=Telegram(Config(telegram_token='123:test',telegram_chat='1'),client);s=signal()
            assert (await tg.send(s))['status']=='enviado'
            body=requests[0].content.decode('utf8','ignore')
            assert '📋 Motor:' not in body and 'rejeição do suporte' not in body
            assert '💱Ativo:' in body
            assert '⬆️Direção: Compra' in body and '⌛Expiração: 1 Minuto' in body
            assert 'Spectro Ai' not in body and 'spectro_ai' not in s
    asyncio.run(run())


def test_engine_explanation_guard_keeps_complete_sentence_and_never_chops_word():
    value=('O setup de PUT em ETH/USDT mostra rejeição na resistência e perda de força compradora. ' * 20)
    fitted=compact_complete_text(value,180)
    assert text_units(fitted)<=180
    assert fitted.endswith('.')
    assert not fitted.endswith('-') and not fitted.endswith('…')


def test_caption_accepts_stored_engine_text_without_240_char_cut():
    s=signal();explanation=('No M5, o preço rejeitou a resistência após uma tentativa de rompimento, ' 
        'enquanto a estrutura de curto prazo perdeu força compradora e favoreceu o setup de venda confirmado pelo motor.')
    assert len(explanation)>120
    s['engine_explanation']={'source':'motor','text':explanation}
    text=caption(s)
    assert explanation not in text and '📋 Motor:' not in text
    assert s['engine_explanation']['text']==explanation
    assert text_units(text)<=1024



def test_preparation_deadline_rechecked():
    async def run():
        sent=[];s=signal()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:sent.append(r))) as client:
            tg=Telegram(Config(telegram_token='123:test',telegram_chat='1'),client)
            clock=iter([s['entry_at']-30,s['entry_at']-5])
            with patch('app.telegram.time.time',side_effect=lambda:next(clock,s['entry_at']-5)):
                assert (await tg.send(s))['status']=='expirado'
            assert not sent
    asyncio.run(run())


@pytest.mark.parametrize('direction,expected',[('CALL','⬆️Direção: Compra'),('PUT','⬇️Direção: Venda')])
def test_compact_unicode_limits(direction,expected):
    s=signal();s.update(direction=direction,duration_seconds=300,engine_explanation={'source':'motor','text':'Texto 🧠 '*500})
    text=caption(s)
    assert text_units(text)<=1024 and expected in text and '5 Minutos' in text


@pytest.mark.parametrize('result,pnl,expected',[('WIN',8,'+R$ 8,00'),('LOSS',-10,'-R$ 10,00'),('EMPATE',0,'+R$ 0,00')])
def test_compact_result(result,pnl,expected):
    async def run():
        sent=[]
        def handle(request):
            sent.append(json.loads(request.content)['text'])
            return httpx.Response(200,json={'ok':True,'result':{'message_id':1}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            s=signal();s.update(result=result,estimated_pnl_brl=pnl)
            await Telegram(Config(telegram_token='123:test',telegram_chat='1'),client).send_result(s,{'wins':8,'losses':2,'ties':1,'win_rate_pct':80})
            assert f'RESULTADO: {result}' in sent[0] and '💰' not in sent[0]
            assert 'Vitórias: 8 · Derrotas: 2' in sent[0] and 'Empates:' not in sent[0]
            assert '80,00%' in sent[0] and 'ID:' not in sent[0]
    asyncio.run(run())
