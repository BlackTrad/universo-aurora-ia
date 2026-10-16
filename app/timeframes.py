"""Native Deriv candle periods exposed by AURORA, in seconds.

These are analysis periods; operation duration is 60, 120, 180 or 300 seconds.
New periods reuse the same closed-candle rules, weights and candle-count windows.
No statistical performance is implied by supporting a period.
"""
from typing import Literal

Timeframe = Literal['M1','M2','M3','M5']
TIMEFRAMES = {'M1':60,'M2':120,'M3':180,'M5':300}

def max_age(timeframe, config):
    return config.max_age if timeframe=='M5' else TIMEFRAMES[timeframe]+30

# Two public requests per asset; each adapter request has an 8-second ceiling.
FETCH_TIMEOUT = 17
CONTRACT_TIMEOUT = 9
