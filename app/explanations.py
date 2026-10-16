"""Versioned text from original engine facts; no API, re-evaluation or outcome input."""
from copy import deepcopy
import re
from .strategies.presentation import strategy_label, NAMES

EXPLANATION_VERSION = '1.2.0'


def text_units(value):
    return len(value.encode('utf-16-le')) // 2


def compact_complete_text(value, budget=520):
    """Bound a caption in Telegram UTF-16 units without chopping words."""
    value = ' '.join(str(value or '').split()).strip()
    if not value or text_units(value) <= budget:
        return value
    best = current = ''
    for index, char in enumerate(value):
        if text_units(current + char) > budget:
            break
        current += char
        if char in '.!?' and (index + 1 == len(value) or value[index + 1].isspace()):
            best = current.strip()
    if best:
        return best
    output = ''
    for word in value.split():
        candidate = (output + ' ' + word).strip()
        if text_units(candidate + '…') > budget:
            break
        output = candidate
    return output.rstrip(' -–—') + '…' if output else 'Texto técnico longo demais para a legenda.'


def original_reasons(signal):
    values = signal.get('reasons', [])
    if not isinstance(values, list):
        return []
    # Presentation deduplication only: never count repeated reasons as confirmation.
    reasons = []
    for value in values:
        if isinstance(value, str) and (clean := ' '.join(value.split())) and clean not in reasons:
            reasons.append(clean)
    return reasons


def _original_text(signal, reasons):
    # Keep original weights in evidence; public text explains conditions only.
    label=NAMES.get(signal.get('strategy_resolved',signal.get('strategy')))
    presented=[]
    for reason in reasons:
        # This exact engine suffix describes score components, not predicates.
        # Removing its numbers alone produced meaningless fragments in Telegram.
        if re.fullmatch(r'Condições obrigatórias confirmadas \(\d+(?:\.\d+)? pts\); corpo \(\d+(?:\.\d+)? pts\); fechamento direcional \(\d+(?:\.\d+)? pts\); amplitude ATR \(\d+(?:\.\d+)? pts\)\. Pontuação heurística, não probabilidade\.',reason):
            continue
        if label and reason.startswith(label+': '):reason=reason[len(label)+2:]
        presented.append(re.sub(r'\s*\(\d+(?:\.\d+)? pts\)', '', reason))
        if len(presented)==3:break
    reason = '; '.join(presented) or 'Motivos técnicos não disponíveis neste registro.'
    return compact_complete_text(f"{signal.get('direction', '')} · {strategy_label(signal)}: {reason}", 480)


def build_explanation(signal):
    """Call once before the operational commit, while only closed candles are known.

    All metadata is copied, not inferred from the eventual price/result. Missing
    evidence stays missing. Expiration is a user parameter, not an engine forecast.
    """
    evidence = signal.get('evidence', {})
    trace = evidence.get('decision_trace', {})
    reasons = original_reasons(signal)
    return {
        'source': 'motor', 'version': EXPLANATION_VERSION,
        'recorded_at': trace.get('recorded_at', signal.get('analysis_at', signal.get('created_at'))),
        'rules_version': evidence.get('rules_version'),
        'pipeline_version': evidence.get('pipeline_version'),
        'strategy': signal.get('strategy_resolved', signal.get('strategy')),
        'direction': signal.get('direction'), 'regime': evidence.get('regime', trace.get('regime')),
        'timeframe': signal.get('timeframe'), 'duration_seconds': signal.get('duration_seconds'),
        'duration_reason': 'Duração selecionada pelo usuário; não comprova resultado na expiração.',
        'score': signal.get('score'), 'score_kind': 'technical_setup_score',
        'score_composition': deepcopy(trace.get('score_composition')),
        'mandatory_criteria': deepcopy(trace.get('mandatory_criteria', [])),
        'manual_source_pages': deepcopy(signal.get('manual_source_pages')),
        'manual_rules_version': signal.get('manual_rules_version'),
        'filters': deepcopy(trace.get('filters', {})),
        'reasons': reasons, 'text': _original_text(signal, reasons),
    }


def technical_summary(signal):
    """Reuse the stored explanation. Legacy text uses only original saved reasons.

    This compatibility view does not write a new explanation into old records.
    Historical AI text is retained in the database but cannot enter a new caption.
    """
    stored = signal.get('engine_explanation')
    if isinstance(stored, dict) and stored.get('source') == 'motor' and isinstance(stored.get('text'), str):
        return compact_complete_text(stored['text'], 480)
    return _original_text(signal, original_reasons(signal))
