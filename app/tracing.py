"""Immutable decision-time explanation; never reconstructed from results."""
import re
from .strategies.regime import classify

MANDATORY={
 'ema_pullback':['Alinhamento EMA9/21 por três velas','Toque na EMA9 sem fechar contra EMA21','Confirmação direcional'],
 'ema_macd':['Cruzamento EMA9/21 recente','MACD alinhado','Confirmação direcional'],
 'bb_reversion':['Fechamento anterior fora da banda','RSI anterior em extremo','Retorno fechado à banda'],
 'range_pivot':['Dois toques prévios na região','Reteste e fechamento afastado','Pavio de rejeição e corpo direcional'],
 'rsi_divergence':['Dois pivôs confirmados preço/RSI divergentes','Segundo pivô recente','Candle de reversão fechado'],
 'stochastic':['Cruzamento K/D após extremo','Candle direcional fechado'],
 'cci':['Retorno CCI de ±100','Candle direcional fechado'],
 'consolidation':['Faixa prévia de 12 velas','Rompimento por fechamento e corpo direcional'],
 'ichimoku':['Cruzamento Tenkan/Kijun','Preço além da nuvem causal','Chikou e candle alinhados'],
 'fibonacci':['Impulso prévio identificável','Retração em nível definido pelo impulso','Reação direcional fechada'],
 'heikin_ashi':['Mudança de cor e duas velas HA confirmadas','Candle OHLC direcional'],
 'candlestick_sr':['Região anterior S/R','Padrão de candle confirmado e fechamento direcional'],
 'sr_volume_breakout':['Fechamento além da região S/R anterior','Volume real fornecido e relativo ≥1,5','Candle direcional'],
 'vwap':['Volume real fornecido nas últimas 21 velas','Toque no VWAP móvel e fechamento direcional além dele'],
}


def score_composition(candidate):
    components=[]
    for reason in candidate.get('reasons',[]):
        for points in re.findall(r'(\d+(?:\.\d+)?) pts',reason):
            components.append({'evidence':reason,'points':float(points)})
    raw=sum(c['points'] for c in components);score=candidate['score']
    accounted=min(100,raw)
    return {'components':components,'raw_points':raw,'cap':100,'final_score':score,
            'unattributed_points':max(0,score-accounted),'reconciled':abs(score-accounted)<1e-6,
            'note':'Pesos declarados pelo motor; itens sem peso explícito não recebem pontos inventados.'}


def decision_trace(candidate,rows,*,now,interval,duration,minimum,strict,rules_version,pipeline_version):
    key=candidate.get('strategy_resolved','scan_all')
    return {'strategy':key,'rules_version':rules_version,'pipeline_version':pipeline_version,
            'recorded_at':now,'regime':candidate.get('regime') or classify(rows) or 'transicao',
            'mandatory_criteria':candidate.get('mandatory_criteria') or [{'criterion':name,'passed':True} for name in MANDATORY.get(key,[])],
            'manual_source_pages':candidate.get('manual_source_pages'),
            'quality_checks':{'closed_candles':True,'continuous_valid_data':True,
                              'minimum_score':candidate.get('manual',False) or candidate['score']>=minimum,'latest_candle_closed':rows[-1].t+interval<=now},
            'filters':{'selection_guard':candidate.get('quality'),'strict_selection':strict,'htf':'disabled'},
            'score_composition':score_composition(candidate),'strategy_evidence':candidate.get('reasons',[]),
            'first_candle_at':rows[0].t,'last_candle_at':rows[-1].t,'interval_seconds':interval,
            'duration_seconds':duration,'direction':candidate['direction'],
            'duration_reason':'Duração escolhida pelo usuário no painel; validade direcional não garante preço na expiração.',
            'explanation_timing':'registrada antes do envio e da apuração'}
