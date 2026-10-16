"""Minute-aligned scheduling with the same notice budget as delivery."""
import math

from .delivery_policy import SEND_TIMEOUT_SECONDS


def next_entry(now, minimum_lead, send_budget=SEND_TIMEOUT_SECONDS):
    # Keep the entire send allowance before the user's manual notice period.
    return math.ceil((now + minimum_lead + send_budget) / 60) * 60


def describe_entry(entry, interval, now, minimum_lead):
    opening = int(entry // interval) * interval
    offset = entry - opening
    if offset == 0:
        phase, label = 'opening', 'Abertura da vela'
    elif offset == interval - 60:
        phase, label = 'last_minute', 'Último minuto da vela'
    else:
        phase, label = 'during_candle', 'Durante a formação da vela'
    return {'version': '1.26.16-entry-1', 'phase': phase, 'label': label,
            'planned_at': now, 'candle_open_at': opening,
            'candle_close_at': opening + interval, 'offset_seconds': offset,
            'alignment': 'minute_open', 'minimum_lead_seconds': minimum_lead,
            'send_budget_seconds': SEND_TIMEOUT_SECONDS}
