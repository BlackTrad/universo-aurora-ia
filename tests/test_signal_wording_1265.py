"""Original predicates stay intact; scores cannot masquerade as win rates."""
from copy import deepcopy
import pytest
from app.explanations import build_explanation, technical_summary
from app.telegram import caption, text_units
from app.win_rate import rate_text


def signal():
    return {'id':'synthetic-wording','asset':'USD/JPY','source_key':'deriv','timeframe':'M5',
            'strategy':'scan_all','strategy_resolved':'rsi_divergence','direction':'CALL',
            'score':90,'entry_at':1791573420,'duration_seconds':300,
            'reasons':['RSI + divergência: Dois pivôs confirmados de preço e RSI(14) em divergência',
                       'Segundo pivô recente com reversão direcional fechada',
                       'Condições obrigatórias confirmadas (70 pts); corpo (8.21 pts); fechamento direcional (8.21 pts); amplitude ATR (0 pts). Pontuação heurística, não probabilidade.']}


def test_caption_uses_predicates_once_and_keeps_original_score_evidence():
    s=signal();original=deepcopy(s)
    explanation=build_explanation(s)
    assert s==original and explanation['reasons']==original['reasons']
    assert explanation['score']==90 and explanation['version']=='1.2.0'
    s['engine_explanation']=explanation
    text=caption(s)
    assert explanation['text'].count('RSI + divergência')==1
    assert 'Dois pivôs confirmados' in explanation['text'] and 'reversão direcional fechada' in explanation['text']
    assert 'corpo;' not in explanation['text'] and ' pts' not in explanation['text']
    assert 'RSI' not in text and '90%' not in text
    assert 'Taxa de acerto' not in text and 'n=0' not in text
    assert '💱Ativo: USD/JPY' in text and '⌛Expiração: 5 Minutos' in text
    assert text_units(text)<=1024


@pytest.mark.parametrize('sample',[0,1,29])
def test_insufficient_sample_does_not_show_any_percentage(sample):
    label,detail=rate_text({'score':99,'observed_win_rate':{'sample':sample,'rate_pct':90,
        'kind':'historical_observed','basis':'indicative_external'}})
    assert '%' not in label and 'indisponível' in label
    assert f'{sample} de 30 resultados necessários' in detail


def test_sufficient_valid_history_is_explicit_and_not_a_prediction():
    label,detail=rate_text({'score':99,'observed_win_rate':{'sample':40,'rate_pct':62.5,
        'kind':'historical_observed','basis':'indicative_external'}})
    assert label=='Taxa de acerto histórica: 62,5%'
    assert '40 resultados apurados' in detail and 'não é previsão' in detail


@pytest.mark.parametrize('rate,basis',[(float('nan'),'indicative_external'),(91,'broker_confirmed'),(False,'indicative_external')])
def test_invalid_snapshot_with_large_sample_explains_unavailable_data(rate,basis):
    label,detail=rate_text({'observed_win_rate':{'sample':50,'rate_pct':rate,'kind':'historical_observed','basis':basis}})
    assert '%' not in label and detail=='Dados históricos incompletos; taxa não calculada.'


def test_existing_explanation_and_original_reasons_are_never_rewritten():
    s=signal();s['engine_explanation']={'source':'motor','version':'1.1.0','text':'Explicação originalmente registrada.'}
    before=deepcopy(s)
    assert technical_summary(s)=='Explicação originalmente registrada.'
    caption(s)
    assert s==before
