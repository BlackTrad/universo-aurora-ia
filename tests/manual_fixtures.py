"""Synthetic closed OHLC illustrations; no actual market performance data."""
from app.market import Candle

START=1791000000
INTERVAL=300


def bar(index,o,h,l,c):
    return Candle(START+index*INTERVAL,o,h,l,c)


def background():
    rows=[]
    for i in range(80):
        phase=i%12
        price=100+.5*(phase if phase<=6 else 12-phase)
        rows.append(bar(i,price-.1,price+.25,price-.25,price+.1))
    return rows


def put(rows,index,values):
    rows[index]=bar(index,*values)


def region_pattern(key):
    rows=background()
    if key in ('pa_engulfing','pa_battle_reversal'):
        put(rows,78,(100.3,100.5,99.75,99.85))
        put(rows,79,(99.85,100.4,99.75,100.35))
    elif key=='pa_engulfing_three':
        put(rows,76,(100.35,100.45,100.1,100.2))
        put(rows,77,(100.2,100.3,99.95,100.05))
        put(rows,78,(100.05,100.15,99.75,99.9))
        put(rows,79,(99.9,100.45,99.75,100.4))
    elif key=='pa_harami':
        put(rows,78,(100.5,100.55,99.75,99.8))
        put(rows,79,(99.9,100.25,99.75,100.15))
    elif key=='pa_star':
        put(rows,77,(100.5,100.55,99.75,99.85))
        put(rows,78,(99.9,100.0,99.75,99.91))
        put(rows,79,(99.9,100.4,99.75,100.35))
    else:
        put(rows,79,(100.05,100.2,99.75,100.15))
    return rows


def distant_zone():
    rows=background()
    for i in range(66,78):put(rows,i,(102.0,102.4,101.8,102.15))
    put(rows,78,(100.7,100.8,100.2,100.35))
    put(rows,79,(99.9,100.45,99.75,100.4))
    return rows


def micro(third=False,flow=False):
    rows=background()
    for i in range(49,79):put(rows,i,(101.5,101.85,101.2,101.65))
    put(rows,50,(99.6,100.0,99.1,99.8))
    put(rows,60,(100.0,100.4,99.4,100.2))
    if third:put(rows,70,(100.1,100.5,99.7,100.3))
    if flow:
        put(rows,78,(100.15,100.3,99.94,100.25))
        put(rows,79,(100.25,100.85,100.2,100.8))
    else:put(rows,79,(100.0,100.35,99.97,100.3))
    return rows


def retest(ninety=False):
    rows=background()
    if ninety:
        put(rows,74,(103.21,103.63,103.19,103.61))
        for i,c in enumerate([103.58,103.54,103.48,103.45],75):
            put(rows,i,(c+.04,c+.07,c-.04,c))
        put(rows,79,(103.3,103.59,103.25,103.54))
    else:
        put(rows,78,(103.05,103.47,103.03,103.45))
        put(rows,79,(103.3,103.7,103.25,103.65))
    return rows


def compression():
    rows=background()
    lows=[101.5,102.1,101.7,102.5,102.0,102.7,102.4,102.8,102.7]
    for i,low in enumerate(lows,70):
        put(rows,i,(102.9,103.25,low,102.98))
    put(rows,79,(103.2,103.64,103.15,103.6))
    return rows


def shape(oco=False):
    rows=background()
    for i in range(40,78):put(rows,i,(100.3,100.7,100.1,100.5))
    # A long completed decline precedes the first bottom.
    for i in range(35,45):
        price=104-(i-35)*.43
        put(rows,i,(price+.1,price+.25,price-.25,price-.1))
    put(rows,45,(99.3,99.6,99.0,99.4))
    if oco:
        put(rows,50,(100.7,101.0,100.5,100.8))
        put(rows,55,(98.2,98.5,98.0,98.4))
        put(rows,60,(100.7,101.0,100.5,100.8))
    else:put(rows,55,(100.7,101.0,100.5,100.8))
    put(rows,65,(99.3,99.6,99.0,99.4))
    put(rows,78,(100.8,101.4,100.7,101.35))
    put(rows,79,(101.1,101.5,101.0,101.45))
    return rows


def fixture(key):
    if key in ('pa_harami','pa_hammer','pa_engulfing','pa_engulfing_three','pa_star','pa_battle_reversal'):
        return region_pattern(key)
    if key in ('pa_v_range','pa_restricted_zone'):
        rows=distant_zone()
        if key=='pa_restricted_zone':put(rows,79,(100.05,100.2,99.75,100.15))
        return rows
    if key=='pa_v_after_trend':
        rows=background()
        for i in range(25):
            p=108-i*.33;put(rows,i,(p+.1,p+.3,p-.2,p-.05))
        for i in range(25,79):put(rows,i,(101.5,102.5,101.2,101.7))
        for i in (25,40,60):put(rows,i,(100.0,100.5,99.75,100.1))
        put(rows,78,(100.7,100.8,100.2,100.35))
        put(rows,79,(99.9,100.45,99.75,100.4))
        return rows
    if key in ('pa_v_micro','pa_micro_second','pa_micro_third','pa_flow_micro'):
        return micro(third=key=='pa_micro_third',flow=key=='pa_flow_micro')
    if key in ('pa_breakout_50','pa_breakout_90'):
        return retest(ninety=key=='pa_breakout_90')
    if key=='pa_pullback_micro':
        rows=micro();put(rows,78,(99.8,100.24,99.78,100.22));put(rows,79,(100.0,100.35,99.97,100.3));return rows
    if key=='pa_pullback_harami':
        rows=retest();put(rows,76,(103.2,103.23,102.65,102.7));put(rows,77,(102.8,103.03,102.75,103.0));return rows
    if key=='pa_flow_zone':
        rows=region_pattern('pa_hammer');put(rows,78,(100.05,100.2,99.75,100.15));put(rows,79,(100.15,100.75,100.1,100.7));return rows
    if key=='pa_flow_recovery':
        rows=background()
        for i in (75,76,77):put(rows,i,(100.1,100.2,99.75,100.12))
        put(rows,78,(100.12,100.7,100.05,100.65));put(rows,79,(100.65,101.2,100.6,101.1));return rows
    if key=='pa_flow_continuation':
        rows=background();put(rows,77,(101.5,101.8,101.2,101.52));put(rows,78,(101.4,102.0,101.35,101.95));put(rows,79,(101.95,102.55,101.9,102.5));return rows
    if key=='pa_compression_breakout':
        rows=compression()
        put(rows,70,(102.9,103.25,100.55,102.98))
        put(rows,68,(103.0,103.25,102.9,103.05));put(rows,69,(103.0,103.25,102.9,103.05))
        put(rows,74,(102.9,103.25,102.6,102.98))
        put(rows,75,(102.9,103.25,102.05,102.98))
        put(rows,76,(102.9,103.25,102.8,102.98));put(rows,77,(103.0,103.25,102.9,103.08))
        put(rows,78,(103.1,103.57,103.08,103.55));put(rows,79,(103.3,103.68,103.25,103.65))
        return rows
    if key in ('pa_flow_compression','pa_breakout_pressure','pa_breakout_lateral'):
        return compression()
    if key=='pa_false_breakout':
        rows=background();put(rows,78,(99.8,99.85,99.4,99.5));put(rows,79,(99.5,100.15,99.45,100.1));return rows
    if key=='pa_micro_exhaustion':
        rows=background()
        for i in range(49,79):put(rows,i,(102.0,102.3,101.8,102.1))
        for i,h in ((50,104.0),(60,103.6),(66,103.36),(72,103.12)):
            put(rows,i,(h-.3,h,h-.5,h-.2))
        put(rows,79,(102.7,104.1,102.65,104.05));return rows
    if key=='pa_wick_retracement':
        rows=background()
        for i in (75,76,77,78):put(rows,i,(100.15,100.25,99.75,100.1))
        put(rows,79,(100.05,100.3,99.75,100.2));return rows
    if key in ('pa_head_shoulders','pa_double_extreme'):
        return shape(oco=key=='pa_head_shoulders')
    raise ValueError(key)
