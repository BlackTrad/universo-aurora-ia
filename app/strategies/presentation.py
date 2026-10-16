from ..manual_book import CATALOG
NAMES={key:spec[0] for key,spec in CATALOG.items()}
NAMES.update(fibonacci='Retração de Fibonacci',scan_all='Price Action do manual',candlestick_sr='Candles em suporte/resistência',ema_pullback='EMA pullback',ema_macd='EMA/MACD',bb_reversion='Bollinger',range_pivot='Pivôs em S/R',auto='Automático anterior',rsi_divergence='Divergência RSI',stochastic='Estocástico',cci='CCI(14)',consolidation='Consolidação',ichimoku='Ichimoku',heikin_ashi='Heikin Ashi',sr_volume_breakout='Rompimento com volume',vwap='VWAP')

def strategy_label(signal):
    key = signal.get('strategy', 'scan_all')
    if key in ('auto','scan_all'):
        resolved = signal.get('strategy_resolved') or signal.get('evidence', {}).get('strategy_resolved')
        return 'Automático → ' + NAMES.get(resolved, 'não informado')
    return NAMES.get(key, 'não informada')
