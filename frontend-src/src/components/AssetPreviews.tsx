import {assetLabel,fmtHMS,tfLabel} from '@/lib/apex';
import {strategyLabel} from '@/lib/presentation';
import {ObservedRate} from './ObservedRate';
import {matchingAnalysis} from '@/lib/analysisSelection';
export {matchingAnalysis} from '@/lib/analysisSelection';

const labels:Record<string,string>={waiting:'Aguardando',analyzing:'Analisando',no_setup:'Sem setup',blocked:'Bloqueado',candidate:'Prévia técnica',emitted:'Sinal registrado',data_error:'Dados indisponíveis',market_closed:'Mercado fechado',engine_error:'Falha na análise',discarded:'Prazo vencido',interrupted:'Interrompido',not_emitted:'Não emitido'};

export function AssetPreviews({snapshot,now,connected,onSelect}:{snapshot:any;now:number;connected:boolean;onSelect:(provider:string,asset:string,tf?:string)=>void}){
 const analysis=matchingAnalysis(snapshot);
 const rows=analysis?.asset_previews||snapshot.settings.assets.map((asset:string)=>({asset,source_key:snapshot.asset_sources?.[asset]||snapshot.provider,timeframe:snapshot.settings.timeframe,status:'waiting',reason:'Inicie uma análise com esta configuração.'}));
 const updated=analysis?.updated_at??analysis?.finished_at??analysis?.created_at;
 const provider=snapshot.source_mode==='automatic'?'Fontes automáticas':snapshot.providers?.find((p:any)=>p.key===snapshot.provider)?.name||snapshot.provider;
 return <section aria-label="Prévia por ativo" className="rounded-lg border border-border bg-card p-3 animate-fade-in" style={{animationDelay:'200ms'}}>
  <div className="mb-2"><p className="text-xs uppercase text-muted-foreground">Última análise · {provider}</p><h2 className="font-semibold">Prévia por ativo</h2>
   <p className="mt-1 text-xs text-muted-foreground">{tfLabel(snapshot.settings.timeframe)} · duração {snapshot.settings.duration_seconds/60} min{updated?` · ${fmtHMS(updated*1000)} BRT`:' · aguardando início'}</p>
  </div>
  <ol className="max-h-80 space-y-2 overflow-y-auto pr-1">{rows.map((row:any)=>{
   const candidate=row.candidate;
   const interval=Number(row.timeframe.slice(1))*60;
   const stale=row.data_at!=null&&now/1000-row.data_at>interval+30;
   const status=!connected?'Sem conexão · última análise':stale&&['candidate','emitted','no_setup','blocked'].includes(row.status)?'Última análise · dados antigos':labels[row.status]||'Não avaliado';
   return <li key={row.source_key+':'+row.asset} data-preview-asset={row.asset} data-preview-status={row.status} className="rounded-md border border-border p-2">
    <button onClick={()=>onSelect(row.source_key,row.asset,row.timeframe)} className="flex w-full items-center justify-between gap-2 text-left text-xs hover:text-primary" aria-label={'Ver '+assetLabel(row.asset)+' no gráfico'}><b className="font-mono">{assetLabel(row.asset)}</b><span className={row.status==='emitted'&&!stale?'text-bull':['data_error','engine_error'].includes(row.status)?'text-bear':'text-muted-foreground'}>{status}</span></button>
    {candidate&&connected&&!stale&&<div className="mt-1 flex flex-wrap gap-x-2 text-xs"><b className={candidate.direction==='CALL'?'text-bull':'text-bear'}>{candidate.direction}</b><span>{strategyLabel(candidate.strategy)}</span><ObservedRate value={candidate.observed_win_rate} compact/></div>}
    <details className="mt-1 text-xs text-muted-foreground"><summary className="cursor-pointer line-clamp-2" title={row.reason}>{row.reason}</summary>
     {row.data_at&&<p className="mt-1">Última vela fechada: {fmtHMS(row.data_at*1000)} BRT</p>}
     {row.evaluated_engines!=null&&<p>{row.evaluated_engines} motores avaliados · {row.candidate_count} candidato(s) detectado(s)</p>}
     {row.detected_strategies?.length>0&&<p>Detectados: {row.detected_strategies.map(strategyLabel).join(', ')}. Detecção não autoriza entrada.</p>}
     {candidate?.reasons?.length>0&&<p>{candidate.reasons.map((r:string)=>r.replace(/\s*\(\d+(?:\.\d+)? pts\)/g,'')).join(' · ')}</p>}
     {row.status==='waiting'&&<p>Prévia e monitoramento usam apenas os ativos habilitados para análise.</p>}
    </details>
   </li>;
  })}</ol>
  <p className="mt-2 text-xs text-muted-foreground">Retrato da análise, sem garantia de entrada. Motivos nos detalhes de cada ativo.</p>
 </section>;
}
