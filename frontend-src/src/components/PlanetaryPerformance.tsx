import {useEffect,useState} from 'react';
import {api} from '@/lib/api';

function number(value:number|null|undefined, suffix=''){
 return value===null||value===undefined?'—':value.toLocaleString('pt-BR')+suffix;
}

export function PlanetaryPerformance({revision}:{revision:string}){
 const [report,setReport]=useState<any>(null);
 const [error,setError]=useState('');
 const [retry,setRetry]=useState(0);
 useEffect(()=>{
  const controller=new AbortController();
  setError('');
  api('/validation/planetary-performance',undefined,undefined,controller.signal)
   .then(data=>{if(!controller.signal.aborted)setReport(data)})
   .catch(err=>{if(!controller.signal.aborted)setError(err.message||'Falha ao carregar auditoria planetária')});
  return ()=>controller.abort();
 },[revision,retry]);
 return <section className="panel-card p-3" aria-label="Auditoria do consenso planetário">
  <div className="flex flex-wrap items-center justify-between gap-2">
   <h2 className="font-semibold">Desempenho planetário · Auditoria</h2>
   <button type="button" className="rounded border border-border px-3 py-1 text-xs" onClick={()=>setRetry(n=>n+1)}>Atualizar</button>
  </div>
  <p className="mt-1 text-xs text-muted-foreground">Dados históricos observacionais. Votos das IAs não são ordens executadas. Apenas preços com evidência consistente entram nas estatísticas direcionais.</p>
  {error&&<p className="mt-2 text-sm text-bear" role="alert">{error}</p>}
  {!report?<p className="mt-3 text-sm text-muted-foreground">{error?'Auditoria indisponível.':'Consultando decisões gravadas…'}</p>:<>
   {report.sample_truncated&&<p role="status" className="mt-2 rounded border border-border p-2 text-xs">Relatório limitado às últimas {number(report.decisions_examined)} de {number(report.source_decision_count)} decisões. Totais não representam todo o histórico.</p>}
   <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
    {[
     ['Pré-sinais técnicos',report.technical_candidates],
     ['Enviados às IAs',report.ai_reviewed],
     ['Consenso aprovado',report.ai_approved],
     ['Consenso rejeitado',report.ai_rejected],
     ['Publicados com prova',report.published_with_price_proof],
     ['Publicados sem prova',report.published_without_price_proof],
     ['Sem setup técnico',report.no_technical_setup],
     ['Decisões verificadas',report.decisions_examined]
    ].map(([label,value])=><div key={String(label)} className="rounded border border-border bg-background/40 p-2"><p className="text-[11px] text-muted-foreground">{label}</p><p className="text-lg font-semibold">{number(value as number)}</p></div>)}
   </div>
   <div className="mt-3 overflow-x-auto">
    <table className="w-full min-w-[560px] text-left text-xs">
     <thead><tr className="border-b border-border text-muted-foreground"><th className="p-2">Planeta</th><th className="p-2">Respostas</th><th className="p-2">Cache</th><th className="p-2">Disponibilidade</th><th className="p-2">Timeout</th><th className="p-2">Latência mediana</th><th className="p-2">P95</th><th className="p-2">Acerto hipotético*</th></tr></thead>
     <tbody>{(report.agents||[]).map((agent:any)=><tr key={agent.name} className="border-b border-border/50"><td className="p-2 font-medium">{agent.name}</td><td className="p-2">{number(agent.calls)}</td><td className="p-2">{number(agent.cache_hits)}</td><td className="p-2">{number(agent.availability_pct,'%')}</td><td className="p-2">{number(agent.timeouts)}</td><td className="p-2">{number(agent.latency_median_ms,' ms')}</td><td className="p-2">{number(agent.latency_p95_ms,' ms')}</td><td className="p-2">{number(agent.hypothetical_direction_match_pct,'%')} <span className="text-muted-foreground">(n={agent.verified_hypothetical_votes})</span></td></tr>)}</tbody>
    </table>
   </div>
   {!report.agents?.length&&<p className="mt-2 text-xs text-muted-foreground">Nenhum voto de IA gravado no período disponível.</p>}
   <details className="mt-3 rounded border border-border p-2 text-xs"><summary className="cursor-pointer font-medium">Estratégias, motivos de rejeição e validação temporal</summary>
    <div className="mt-2 space-y-3">
     <div><p className="font-semibold">Estratégias técnicas</p>{(report.strategies||[]).map((s:any)=><p key={s.strategy}>{s.strategy}: {s.candidates} pré-sinais · {s.ai_reviewed} analisados · {s.ai_rejected} rejeitados · {s.verified_published} publicados com prova</p>)}</div>
     <div><p className="font-semibold">Motivos registrados pelo consenso</p>{(report.ai_rejection_reasons||[]).map((r:any)=><p key={r.code}>{r.code}: {r.count}</p>)}</div>
     <div><p className="font-semibold">Separação temporal 70/30 (descritiva)</p><p>Treino com resultado conhecido antes do início do teste: {report.holdout.causally_available_train.n} casos; taxa observada {number(report.holdout.causally_available_train.observed_win_pct,'%')}.</p><p>Período reservado: {report.holdout.holdout_published.n} casos; taxa observada {number(report.holdout.holdout_published.observed_win_pct,'%')}.</p><p className="text-muted-foreground">Sem ajuste automático de pesos; comparação causal entre motor original e IAs não disponível.</p></div>
    </div>
   </details>
   <p className="mt-2 text-[11px] text-muted-foreground">* Concordância hipotética com o movimento de preço apenas em sinais publicados e comprovados; não mede operações adicionais nem desempenho da corretora. Rejeitados não possuem liquidação observável. Nenhum indicador garante acerto futuro.</p>
  </>}
 </section>;
}
