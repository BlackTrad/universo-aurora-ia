import {tfLabel} from '@/lib/apex';

export function DashboardHero({assetCount, timeframe}: {assetCount?: number; timeframe?: string}) {
  return <section className="dashboard-hero" aria-labelledby="dashboard-title">
    <div className="dashboard-hero-copy">
      <p className="dashboard-eyebrow"><span aria-hidden="true"/> AURORA <span aria-hidden="true">/</span> OPÇÕES BINÁRIAS</p>
      <h1 id="dashboard-title">Painel de sinais<span className="hero-dot">.</span></h1>
      <p className="dashboard-hero-description">Análise de entradas, sinais e acompanhamento.<br/>Tudo em um só lugar.</p>
      <div className="dashboard-context" aria-label="Configuração atual da análise">
        <span>Fontes automáticas</span>{assetCount !== undefined && <span>{assetCount} {assetCount === 1 ? 'ativo selecionado' : 'ativos selecionados'}</span>}{timeframe && <span>{tfLabel(timeframe)}</span>}
      </div>
    </div>
    <span className="dashboard-orbit-caption" aria-hidden="true"><span/> UNIVERSO AURORA</span>
  </section>;
}
