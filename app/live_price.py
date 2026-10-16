"""Timestamped public prices for intrabar observation, never fabricated OHLC."""
from dataclasses import asdict, dataclass
import math
from .market import DataError

MAX_PRICE_AGE_SECONDS = 10
POLL_SECONDS = 15


def point_after(point, previous):
    """Equal source seconds are ordered by actual receipt, never fake epochs."""
    if (point.at, point.received_at) <= (previous.at, previous.received_at): return False
    if point.at < previous.at: return False
    if point.trade_id is not None and previous.trade_id is not None:
        if point.trade_id == previous.trade_id: return False
        if point.trade_id.isdecimal() and previous.trade_id.isdecimal() and int(point.trade_id) <= int(previous.trade_id): return False
    return True


@dataclass(frozen=True)
class PricePoint:
    price: float
    at: float
    received_at: float
    asset: str
    source_identity: str
    trade_id: str | None = None

    def json(self):
        return asdict(self)


def validate_point(point, *, asset, identity, now):
    if not isinstance(point, PricePoint):
        raise DataError('Cotação intravela sem prova de horário.', 'live_price_invalid')
    if point.trade_id is not None and not isinstance(point.trade_id,str):
        raise DataError('Identificador de negócio inválido.', 'live_price_invalid')
    if any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0
           for value in (point.price, point.at, point.received_at, now)):
        raise DataError('Cotação intravela inválida.', 'live_price_invalid')
    if point.asset != asset or point.source_identity != identity:
        raise DataError('Cotação de outro instrumento ou fonte.', 'source_mismatch')
    def timing_error(message, code):
        error=DataError(message,code)
        error.price_timing={'source_at':point.at,'received_at':point.received_at,'checked_at':now,
                            'source_minus_receipt_seconds':round(point.at-point.received_at,6),
                            'receipt_minus_check_seconds':round(point.received_at-now,6),
                            'age_at_check_seconds':round(now-point.at,6)}
        return error
    if point.at > point.received_at or point.received_at > now or point.at > now:
        raise timing_error('Cotação intravela com horário futuro.', 'live_price_future')
    if now-point.at > MAX_PRICE_AGE_SECONDS or now-point.received_at > MAX_PRICE_AGE_SECONDS:
        raise timing_error('Preço intravela atrasado; entrada bloqueada.', 'live_price_stale')
    return point
