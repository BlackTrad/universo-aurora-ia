export function ObservedRate({value,compact=false,inline=false,decisionSnapshot=true}:{value:any;compact?:boolean;inline?:boolean;decisionSnapshot?:boolean}){
 const n=Number.isInteger(value?.sample)&&value.sample>=0?value.sample:0;
 const rate=value?.rate_pct;
 const valid=value?.kind==='historical_observed'&&value?.basis==='indicative_external'&&n>=30&&typeof rate==='number'&&Number.isFinite(rate)&&rate>=0&&rate<=100;
 const ci=Array.isArray(value?.wilson_95_pct)?' · intervalo 95% '+value.wilson_95_pct.join('–')+'%':'';
 return <span data-testid="observed-rate" title={'Vitórias divididas por vitórias mais derrotas deste setup, com o mesmo ativo, fonte, período e duração. '+(decisionSnapshot?'Registrado antes da entrada':'Calculado no corte do relatório')+ci+'. Resultado indicativo da fonte externa; não é previsão.'}>
  {!compact&&'Taxa de acerto histórica: '}<b>{valid?rate.toLocaleString('pt-BR',{maximumFractionDigits:1})+'%':n<30?'Histórico insuficiente':'Taxa indisponível'}</b>
  <span className={(inline?'ml-2 inline':'block')+' text-[10px] font-normal text-muted-foreground'}>{valid?`${n} resultados · fonte externa`:n<30?`${n} de 30 resultados necessários`:'Dados históricos incompletos'}</span>
 </span>
}
