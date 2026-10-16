"""Exact user-requested message formats, with simulated delivery only."""
from io import BytesIO
from copy import deepcopy
import pytest
from PIL import Image,ImageDraw
from app.telegram import caption,card,result_caption


def signal(direction='CALL',duration=300):
    return {'id':'synthetic-only','asset':'USD/JPY','direction':direction,'duration_seconds':duration,
            'entry_at':1791573420,'expiry_at':1791573420+duration,'timeframe':'M5','source_key':'deriv',
            'source':'Deriv','score':99,'result':'WIN','state':'encerrado','entry_price':158.241,
            'expiry_price':158.267,'reasons':['Contexto original'],'estimated_pnl_brl':20,
            'engine_explanation':{'source':'motor','text':'Explicação preservada no painel.'},
            'observed_win_rate':{'sample':100,'rate_pct':90}}


def test_exact_call_entry_format_and_original_context_preserved():
    s=signal();before=deepcopy(s)
    assert caption(s)=='💱Ativo: USD/JPY\n🕐Entrada: 16:17\n⬆️Direção: Compra\n⌛Expiração: 5 Minutos\n\n⛔SEM MARTINGALE⛔'
    assert s==before


@pytest.mark.parametrize('direction,arrow,name',[('CALL','⬆️','Compra'),('PUT','⬇️','Venda')])
@pytest.mark.parametrize('duration',[60,120,180,300])
def test_both_directions_and_duration_are_the_original_signal_parameters(direction,arrow,name,duration):
    text=caption(signal(direction,duration))
    assert arrow+'Direção: '+name in text
    assert f'Expiração: {duration//60} '+('Minuto\n' if duration==60 else 'Minutos\n') in text
    assert '📋 Motor' not in text and '%' not in text and '99' not in text


def test_exact_win_result_format():
    assert result_caption(signal(),{'wins':1,'losses':0,'ties':0,'win_rate_pct':100})==(
        '✅ RESULTADO: WIN ✅\n\n💱Ativo: USD/JPY\n🕐Entrada: 16:17\n⬆️Direção: Compra\n'
        '⌛Expiração: 5 Minutos\n\n🕔Fim: 09/10 16:22:00 · Brasília\n'
        '🏆Vitórias: 1 · Derrotas: 0\n🎯Taxa de acerto: 100,00%')


def test_loss_tie_and_unavailable_are_never_formatted_as_win():
    for outcome,icon in [('LOSS','❌'),('EMPATE','➖'),('Resultado indisponível','⚪')]:
        s=signal('PUT');s['result']=outcome
        text=result_caption(s,{'wins':0,'losses':1,'ties':1,'win_rate_pct':0})
        assert text.startswith(f'{icon} RESULTADO: {outcome} {icon}') and 'Direção: Venda' in text
        assert 'Resultado estimado' not in text and 'Preços da fonte' not in text and 'Apuração pela' not in text


def test_card_contains_only_the_five_requested_fields(monkeypatch):
    drawn=[];original=ImageDraw.ImageDraw.text
    def capture(self,xy,text,*args,**kwargs):
        drawn.append(text);return original(self,xy,text,*args,**kwargs)
    monkeypatch.setattr(ImageDraw.ImageDraw,'text',capture)
    png=card(signal())
    assert drawn==['Ativo: USD/JPY','Entrada: 16:17','Direção: ↑ Compra','Expiração: 5 Minutos','SEM MARTINGALE']
    assert Image.open(BytesIO(png)).size==(1080,860)
