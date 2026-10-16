"""Price/time proofs shared by metrics and causal historical preferences."""
import math


PROOF_VERSION = '1.26.2-m1-closed-1'


def comparable_outcome(row, now):
    if row.get('state') != 'encerrado' or row.get('settlement_pending'):
        return False
    if row.get('result') not in ('WIN', 'LOSS', 'EMPATE'):
        return False
    keys = ('created_at', 'entry_at', 'expiry_at', 'settled_at', 'entry_price', 'expiry_price')
    if any(type(row.get(k)) not in (int, float) or not math.isfinite(row[k]) or row[k] <= 0 for k in keys):
        return False
    if not row['created_at'] <= row['entry_at'] < row['expiry_at'] <= row['settled_at'] <= now:
        return False
    if row['expiry_at'] - row['entry_at'] != row.get('duration_seconds'):
        return False
    if row['entry_at'] % 60 or row['expiry_at'] % 60 or row['expiry_at'] + 60 > row['settled_at']:
        return False
    if (not isinstance(row.get('source_identity'),str) or not row['source_identity'] or row.get('settlement_source_identity') != row['source_identity']
            or row.get('settlement_entry_at') != row['entry_at'] or row.get('settlement_expiry_at') != row['expiry_at']):
        return False
    if row.get('direction') not in ('CALL', 'PUT'):
        return False
    change = row['expiry_price'] - row['entry_price']
    expected = 'EMPATE' if change == 0 else 'WIN' if (change > 0) == (row['direction'] == 'CALL') else 'LOSS'
    if row['result'] != expected:
        return False
    quotes = row.get('settlement_evidence')
    if not isinstance(quotes, list) or len(quotes) != 2:
        return False
    for quote in quotes:
        if (not isinstance(quote, dict) or type(quote.get('t')) is not int or quote['t'] <= 0 or quote['t'] % 60
                or any(type(quote.get(k)) not in (int, float) or not math.isfinite(quote[k]) or quote[k] <= 0
                       for k in ('o', 'h', 'l', 'c'))
                or (quote.get('v') is not None and (type(quote['v']) not in (int,float) or not math.isfinite(quote['v']) or quote['v']<0))
                or not quote['l'] <= min(quote['o'], quote['c']) <= max(quote['o'], quote['c']) <= quote['h']):
            return False
    return (quotes[0]['t'] == row['entry_at'] and quotes[1]['t'] == row['expiry_at']
            and quotes[0]['o'] == row['entry_price'] and quotes[1]['o'] == row['expiry_price'])


def verified_projection(row, now):
    """Use only a server-derived compact proof, produced from original quotes.

    This is for the private SQLite metric projection, never for raw signals or
    caller-supplied confirmations. Rebuild on PROOF_VERSION changes.
    """
    proof = row.get('price_evidence') or {}
    return (proof.get('version') == PROOF_VERSION and proof.get('verified') is True
            and row.get('state') == 'encerrado' and not row.get('settlement_pending')
            and row.get('result') in ('WIN', 'LOSS', 'EMPATE')
            and type(row.get('settled_at')) in (int, float) and row['settled_at'] <= now)


def research_outcome(row, now):
    """Verify existing M1 evidence; never infer absent quotes or alter origins."""
    if row.get('state') != 'settled' or not row.get('accepted'):
        return False
    quotes = row.get('settlement_evidence')
    if not isinstance(quotes, list) or len(quotes) != 2:
        return False
    for quote in quotes:
        if not isinstance(quote, dict):
            return False
        if (type(quote.get('t')) is not int or quote['t'] <= 0 or quote['t'] % 60
                or any(type(quote.get(k)) not in (int, float) or not math.isfinite(quote[k]) or quote[k] <= 0 for k in ('o', 'h', 'l', 'c'))
                or not quote['l'] <= min(quote['o'], quote['c']) <= max(quote['o'], quote['c']) <= quote['h']):
            return False
    opening, closing = quotes
    settled = row.get('settled_at')
    if type(settled) not in (int, float) or closing['t'] + 60 > settled:
        return False
    if (opening['t'] != row.get('entry_at') or closing['t'] != row.get('expiry_at')
            or opening['o'] != row.get('entry_price') or closing['o'] != row.get('expiry_price')):
        return False
    return comparable_outcome({**row, 'state': 'encerrado', 'settlement_pending': False,
                               'settlement_entry_at': opening['t'], 'settlement_expiry_at': closing['t']}, now)


def research_metric_view(row, now):
    """A causal report view; never write corrected labels over originals."""
    settled=row.get('settled_at')
    if type(settled) in (int,float) and math.isfinite(settled) and settled>now:
        return {**row,'state':'pending_result','result':None,'registered_result':None,'settled_at':None,
                'entry_price':None,'expiry_price':None,'settlement_evidence':None,
                'outcome_proof_status':'pending_at_cutoff'}
    if row.get('state')=='settled' and row.get('result') in ('WIN','LOSS','EMPATE'):
        if research_outcome(row,now):
            return {**row,'outcome_proof_status':'price_verified_indicative'}
        return {**row,'registered_result':row['result'],'state':'unavailable','result':None,
                'outcome_proof_status':'invalid_or_missing_price_proof'}
    return row
