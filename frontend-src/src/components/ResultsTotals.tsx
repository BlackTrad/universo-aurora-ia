export function ResultsTotals({snapshot}:{snapshot:any}){
 const totals=snapshot.result_totals;
 const items=[['Vitórias',totals?.wins??0,'text-bull'],['Derrotas',totals?.losses??0,'text-bear'],['Empates',totals?.ties??0,'text-warn'],['Vitórias observadas',totals?.win_rate_pct==null?'—':totals.win_rate_pct.toFixed(2)+'%','text-primary']];
 return <section aria-label="Resultados dos sinais" className="panel-card learning-card p-3">
  <p className="mb-2 text-xs text-muted-foreground">{totals?.scope||'Histórico visível · indicativo'}</p>
  <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">{items.map(([label,value,color])=><div key={label} className="rounded bg-background/50 p-2"><span className="text-xs text-muted-foreground">{label}: </span><b className={'font-mono text-lg '+color}>{value}</b></div>)}</div>
  <p className="mt-2 text-xs text-muted-foreground">Vitórias / (vitórias + derrotas); empates excluídos. Histórico observado, sem previsão de acerto.{totals?.unverified>0&&<span className="text-warn"> Sem comprovação: {totals.unverified}.</span>}</p>
 </section>;
}
