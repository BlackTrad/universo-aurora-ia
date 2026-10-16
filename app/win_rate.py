"""Public historical-rate labels; no conversion from a technical score."""
import math


def rate_text(signal):
    stats=signal.get('observed_win_rate') or {}
    sample=stats.get('sample',0)
    sample=sample if type(sample) is int and sample>=0 else 0
    rate=stats.get('rate_pct')
    valid=(stats.get('kind')=='historical_observed' and stats.get('basis')=='indicative_external'
           and sample>=30 and type(rate) in (int,float) and math.isfinite(rate) and 0<=rate<=100)
    if not valid:
        detail=(f'{sample} de 30 resultados necessários (vitórias/derrotas) deste setup; ainda sem porcentagem.'
                if sample<30 else 'Dados históricos incompletos; taxa não calculada.')
        return 'Taxa de acerto histórica: indisponível',detail
    return (f'Taxa de acerto histórica: {rate:.1f}%'.replace('.',','),
            f'{sample} resultados apurados (vitórias/derrotas) · indicativos da fonte externa; não é previsão')
