"""Hand-built book drawings and actual sampled-price examples, test data only."""
from app.market import Candle
from tests.manual_fixtures import background,bar,put,fixture as legacy,micro


def scenario(key,direction='CALL'):
    prices=[];opening=None;native='CALL'
    aliases={'pa_flow_zone':'pa_hammer','pa_flow_harami':'pa_harami',
             'pa_flow_engulfing':'pa_engulfing','pa_flow_hammer':'pa_hammer','pa_flow_star':'pa_star',
             'pa_battle_wick':'pa_battle_reversal'}
    if key in aliases:
        rows=legacy(aliases[key])
        if key.startswith('pa_flow_'):prices=[rows[-1].c+.10,rows[-1].c+.15]
    elif key=='pa_pullback_harami':
        rows=legacy('pa_compression_breakout')
        put(rows,76,(103.2,103.23,102.65,102.7));put(rows,77,(102.8,103.03,102.75,103.0))
    elif key=='pa_false_breakout':
        rows=background()
        put(rows,74,(99.95,100.2,99.8,100.05));put(rows,75,(100.2,100.25,99.35,99.4))
        for i,c in zip((76,77,78),(99.45,99.5,99.6)):put(rows,i,(c-.03,c+.04,99.35,c))
        put(rows,79,(99.6,100.2,99.55,100.1))
    elif key=='pa_breakout_pattern':
        rows=background();put(rows,77,(102.3,102.5,102.1,102.32))
        put(rows,78,(102.4,103.1,102.38,103.08));put(rows,79,(103.2,103.85,103.18,103.8))
    elif key=='pa_breakout_near_miss':
        rows=background();put(rows,73,(102.5,103.03,102.4,102.97))
        put(rows,74,(102.95,103.0,102.35,102.45));put(rows,75,(102.45,102.8,102.25,102.48))
        put(rows,76,(102.5,102.9,102.4,102.75));put(rows,77,(102.8,103.0,102.7,102.9))
        put(rows,78,(102.95,103.14,102.85,103.08));put(rows,79,(103.1,103.64,103.05,103.6))
    elif key=='pa_battle_body':
        rows=background();put(rows,79,(100.15,100.6,99.9,100.5))
    elif key=='pa_micro_fourth':
        rows=micro(third=True)
        for i in (16,28,40):put(rows,i,(100.3,100.8,100.28,100.5))
        for i in range(80,90):rows.append(bar(i,101.5,101.9,101.25,101.65))
        rows.append(bar(90,100.55,100.63,100.3,100.6))
    elif key=='pa_flow_micro':
        rows=micro(flow=True)
        rows.append(bar(80,100.7,101.0,100.6,100.9));rows.append(bar(81,100.8,101.0,100.6,100.9))
        rows.append(bar(82,100.3,100.44,100.06,100.4));prices=[100.50,100.55]
    elif key=='pa_flow_compression':
        rows=legacy('pa_compression_breakout')[:78];opening=103.1;prices=[103.30,103.43]
    elif key=='pa_correction_flow':
        rows=micro()[:78]
        for i in (16,28,40):put(rows,i,(100.9,101.2,100.7,101.1))
        put(rows,77,(101.65,101.7,101.2,101.25));prices=[101.0,100.80];native='PUT'
    else:
        rows=legacy(key)
        if key=='pa_flow_recovery':prices=[rows[-1].c+.10,rows[-1].c+.15]
        if key=='pa_flow_continuation':prices=[rows[-1].c+.06,rows[-1].c+.10]
        if key=='pa_wick_retracement':prices=[99.76,99.84]
    if direction!=native:
        mirror=2*max(c.h for c in rows)
        rows=[Candle(c.t,mirror-c.o,mirror-c.l,mirror-c.h,mirror-c.c,c.v) for c in rows]
        prices=[mirror-p for p in prices]
        if opening is not None:opening=mirror-opening
    return {'rows':rows,'prices':prices,'forming_open':opening,'direction':direction}
