export function signalStage(s:any,now:number){
 if(s.state==='cancelado'||s.result==='CANCELADO')return {label:'Entrada cancelada',tone:'bear'};
 const entry=new Date(s.entry_time).getTime(),expiry=new Date(s.expiry_time).getTime();
 if(s.state==='encerrado'&&!s.settlement_pending)return {label:['WIN','LOSS','EMPATE'].includes(s.result)?s.result:'Resultado indisponível',tone:s.result==='WIN'?'bull':s.result==='LOSS'?'bear':'warn'};
 if(now>=expiry||s.result==='APURANDO')return {label:'Apurando resultado',tone:'warn'};
 if(s.telegram?.lead_status==='late')return {label:'Envio atrasado · não entrar',tone:'bear'};
 if(now>=entry||s.state==='em andamento')return {label:s.entry_validation&&s.entry_validation.status!=='confirmada'?'Confirmação ausente · não entrar':'Em andamento',tone:'warn'};
 if(s.entry_validation?.status==='confirmada')return {label:'Entrada confirmada',tone:'bull'};
 if(s.entry_validation?.status==='pendente')return {label:now>=s.entry_validation.scheduled_at*1000?'Revalidando entrada':'Aguardando confirmação',tone:'warn'};
 return {label:'Aguardando entrada',tone:'primary'};
}
export function telegramStage(value:any){
 if(value?.lead_status==='late')return 'Enviado com atraso';
 const names:Record<string,string>={enviado:'Enviado',pendente:'Aguardando envio',enviando:'Enviando',repetir:'Nova tentativa agendada',incerto:'Confirmação incerta',falha:'Falha no envio',expirado:'Prazo de envio vencido','não configurado':'Não configurado'};
 return names[value?.status]||'Não verificado';
}
