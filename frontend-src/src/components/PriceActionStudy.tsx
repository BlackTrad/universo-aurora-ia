import {fmtDateTime} from '@/lib/apex';

export function PriceActionStudy({study,loading=false}:{study:any;loading?:boolean}){
 const latest=study?.latest,checks=latest?.context_evidence?.candidate_checks||[];
 return <section aria-label="Estudo de Price Action para opções binárias" className="space-y-3 rounded border border-border bg-card p-4">
  <div><h2 className="font-semibold">Price Action para opções binárias</h2><p className="mt-1 text-xs text-muted-foreground">Comparação experimental em M1/M5. Sem envio ao Telegram ou ativação automática.</p></div>
  {!study?<p className="text-sm">{loading?'Carregando estudo...':'Habilite a coleta e inicie a análise para registrar decisões.'}</p>:<>
   {study.status==='legacy_session'&&<p className="text-xs text-muted-foreground">Configuração anterior ao seletor por regime. Os registros originais permanecem separados.</p>}
   <p className="text-sm">Avaliações do seletor: <b>{study.evaluations}</b> · Escolhas técnicas: <b>{study.selected}</b> · Sem oportunidade válida: <b>{study.no_opportunity}</b></p>
   <div className="max-w-full overflow-x-auto"><table className="w-full text-left text-xs"><caption className="sr-only">Coleta dos setups experimentais</caption><thead><tr className="border-b border-border"><th className="py-2 pr-3">Setup</th><th className="p-2">Detectados</th><th className="p-2">Rejeitados</th><th className="p-2">Escolhidos</th></tr></thead><tbody>{study.setups.map((s:any)=><tr key={s.strategy} className="border-b border-border/50"><td className="py-2 pr-3">{s.label}</td><td className="p-2">{s.detected}</td><td className="p-2">{s.rejected}</td><td className="p-2">{s.chosen}</td></tr>)}</tbody></table></div>
   <p className="text-xs text-muted-foreground">Detecção e escolha ainda podem ser canceladas antes da entrada. Resultados ficam na tabela abaixo, separados por ativo, fonte, timeframe, expiração e versão.</p>
   {latest&&<details className="rounded border border-border p-3"><summary className="cursor-pointer text-sm">Última decisão · {latest.asset} · {fmtDateTime(latest.created_at*1000)}</summary><p className="mt-2 text-sm">{latest.reason}</p>{latest.cancellation_reason&&<p className="mt-1 text-xs text-warn">{latest.cancellation_reason}</p>}<ul className="mt-2 space-y-1 text-xs">{checks.map((c:any)=><li key={c.strategy} className="break-words"><b>{c.label}:</b> {c.reason}</li>)}</ul></details>}
  </>}
  <p className="text-xs text-muted-foreground">Tendência: pullback ou rompimento alinhado. Lateralização: reversão na faixa. Transição: rompimento e reteste. Mais de um setup elegível bloqueia a escolha; score e quantidade de indicadores não desempatarão.</p>
  <p className="text-xs text-muted-foreground">Terceiro toque: a abertura seguinte permanece original. Sem antecedência após o fechamento, o setup é descartado.</p>
 </section>
}
