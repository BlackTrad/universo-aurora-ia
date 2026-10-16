"""One lead-time policy before preparation, after queueing and on API ACK."""
SEND_TIMEOUT_SECONDS = 12
MINIMUM_LEAD_SECONDS = 60


def minimum_lead(signal):
    value=(signal.get('evidence') or {}).get('configuration',{}).get('min_lead_seconds',MINIMUM_LEAD_SECONDS)
    return value if type(value) is int and 60<=value<=180 else MINIMUM_LEAD_SECONDS


def can_start(signal, now):
    return now+SEND_TIMEOUT_SECONDS+minimum_lead(signal)<=signal['entry_at']


def classify_ack(signal, result, now, started_at):
    result={**result,'at':now,'delivery_started_at':started_at,'minimum_lead_seconds':minimum_lead(signal)}
    if result.get('status')=='enviado':
        lead=signal['entry_at']-now
        actionable=lead>=minimum_lead(signal) and signal.get('state')!='cancelado'
        result.update(ack_at=now,lead_at_ack_seconds=round(lead,3),actionable_at_ack=actionable,
                      lead_status='within_minimum' if actionable else 'late')
        if not actionable:
            # An accepted photo must remain delivered; never retry it as failed.
            result['detail']='Telegram confirmou a mensagem sem antecedência suficiente ou após cancelamento. Não executar esta entrada.'
    return result
