import {assetLabel,fmtHMS,tfLabel} from '@/lib/apex';
import {strategyLabel} from '@/lib/presentation';

export function LearningStatus({value}: {value:any}){
 const last=value?.last_feedback;
 return <details className="rounded-lg border border-border bg-card p-3" aria-label="Aprendizado por histórico">
  <summary className="cursor-pointer text-sm font-semibold">Aprendizado por histórico <span className="font-normal text-muted-foreground">· {value?.status||'coletando resultados'}</span></summary>
  <div className="mt-3 space-y-2 text-xs text-muted-foreground">
   <p>O histórico é atualizado após cada apuração. Preferências e vetos exigem 60 resultados de treino e 30 de validação no mesmo contexto, sem operações sobrepostas.</p>
   <p>{value?.eligible_profiles||0} contexto(s) com preferência · {value?.blocked_profiles||0} com veto. Maior amostra por contexto: {value?.max_context_sample||0} resultados.</p>
   {last?<><p>Último retorno: <b>{assetLabel(last.asset)} · {last.result}</b> · {fmtHMS(last.known_at*1000)} BRT</p>
    <p>{strategyLabel(last.strategy)} · {tfLabel(last.timeframe)} · duração {last.duration_seconds/60} min · {last.source_key}</p>
    <p>{last.context_sample} resultados nesse contexto · treino {last.train_sample} · validação {last.validation_sample}. {last.preference_enabled?'Preferência estatística habilitada.':last.veto_enabled?'Veto estatístico habilitado.':'Sem ajuste por este resultado isolado.'}</p>
    <p>{last.interpretation}</p></>:<p>Nenhum retorno registrado nesta versão. A análise não começa automaticamente.</p>}
   <p>Resultado indicativo da fonte externa. Nenhuma regra de entrada é reescrita por uma perda isolada; melhora a cada sinal não é garantida.</p>
  </div>
 </details>;
}
