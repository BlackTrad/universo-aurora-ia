"""Replay correctness checks; synthetic data is never included in market reports."""
from app.market import Candle
from scripts.backtest import aggregate,simulate


def candidate(t):return {'direction':'CALL','score':85,'setup_candle':t}


def test_aggregation_does_not_fill_missing_minutes():
    rows=[Candle(t,100,101,99,100) for t in (120,180,240,360,420)]
    result=aggregate(rows,120)
    assert [c.t for c in result]==[120,360]


def test_schedule_waits_for_minimum_lead_and_exact_expiry():
    trades,_=simulate([(302,[('EUR/USD',candidate(300))])],{'EUR/USD':{420:100,480:101}},300,60,900,['EUR/USD'])
    assert trades[0]['entry_at']==420 and trades[0]['expiry_at']==480
    assert trades[0]['result']=='WIN'
    # A nearby quote must not replace the quote at exact expiration.
    trades,_=simulate([(302,[('EUR/USD',candidate(300))])],{'EUR/USD':{420:100,540:101}},300,60,900,['EUR/USD'])
    assert trades[0]['result']=='INDISPONIVEL'


def test_loss_pause_only_uses_results_available_at_decision():
    events=[(t,[('EUR/USD',candidate(t))]) for t in (2,62,200,310,1400)]
    prices={120:100,180:99,240:98,300:100,360:99,1500:100,1560:101}
    trades,blocks=simulate(events,{'EUR/USD':prices},60,60,1800,['EUR/USD'])
    assert [t['created_at'] for t in trades]==[2,62,200,1400]
    assert blocks['pausa_2_losses']==1


def test_hourly_limit_is_across_assets_and_one_signal_per_cycle():
    events=[(t,[(asset,candidate(t)) for asset in ('EUR/USD','GBP/USD')]) for t in range(2,482,60)]
    prices={t:100+t/60 for t in range(0,1200,60)}
    trades,blocks=simulate(events,{'EUR/USD':prices,'GBP/USD':prices},60,60,1500,['EUR/USD','GBP/USD'])
    assert len(trades)==5 and all(t['asset']=='EUR/USD' for t in trades)
    assert blocks['limite_5_hora']==6


def test_every_strategy_timeframe_and_expiry_is_replayed():
    import math
    from app.strategies import STRATEGIES
    from scripts.backtest import run
    base=1800000000
    candles=[Candle(base+i*60,100+.1*math.sin(i),100.5,99.5,100+.1*math.sin(i+1)) for i in range(610)]
    groups,trades=run({'EUR/USD':candles},base+30000,base+36600,['EUR/USD'])
    assert len(groups)==len(STRATEGIES)*4*4
    assert all(group['valid_scans']>0 for group in groups)
    assert all(group['status']=='volume_unavailable' for group in groups if group['strategy'] in ('vwap','sr_volume_breakout'))


def test_history_parser_keeps_numeric_strings_and_missing_volume():
    from scripts.backtest import parse_history_candle
    assert parse_history_candle(dict(epoch='120',open='100',high='101',low='99',close='100'),179)==Candle(120,100,101,99,100)


def test_history_parser_explains_invalid_fields_and_boundaries():
    import pytest
    from app.market import DataError
    from scripts.backtest import parse_history_candle
    row=dict(epoch=120,open=100,high=101,low=99,close=100)
    for patch,code in [({'epoch':121},'invalid_history_timestamp'),
                       ({'epoch':180},'history_out_of_range'),
                       ({'open':True},'invalid_history_field'),
                       ({'close':float('nan')},'invalid_history_field'),
                       ({'volume':-1},'invalid_history_ohlc'),
                       ({'low':102},'invalid_history_ohlc')]:
        with pytest.raises(DataError) as exc:parse_history_candle({**row,**patch},179)
        assert exc.value.code==code
    with pytest.raises(DataError,match='objeto'):parse_history_candle(None,179)


def test_download_failure_records_public_diagnostic_only(monkeypatch):
    import asyncio
    from scripts.backtest import download,DerivProvider
    async def request(self,payload,expected):
        return {'candles':[{'epoch':120,'open':True,'high':101,'low':99,'close':100,'private':'omit'}],
                'private':'omit'}
    monkeypatch.setattr(DerivProvider,'request',request)
    data,errors=asyncio.run(download(['EUR/USD'],120,180))
    assert data=={'EUR/USD':[]}
    error=errors['EUR/USD']
    assert error['code']=='invalid_history_field' and 'open' in error['detail']
    assert error['context']['page']==1 and error['context']['row_index']==0
    assert 'private' not in str(errors)


def test_download_success_keeps_valid_candles(monkeypatch):
    import asyncio
    from scripts.backtest import download,DerivProvider
    async def request(self,payload,expected):
        return {'candles':[dict(epoch=t,open=100,high=101,low=99,close=100) for t in (60,30060)]}
    monkeypatch.setattr(DerivProvider,'request',request)
    data,errors=asyncio.run(download(['EUR/USD'],30060,30120))
    assert not errors and [c.t for c in data['EUR/USD']]==[60,30060]
