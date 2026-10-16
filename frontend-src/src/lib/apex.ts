export type Signal=any;
export type Settings=any;
export const assetLabel=(s:string)=>s;
export const tfLabel=(s:string)=>s==='1m'?'M1':s==='5m'?'M5':s;
const brt=(opts:Intl.DateTimeFormatOptions)=>new Intl.DateTimeFormat('pt-BR',{timeZone:'America/Sao_Paulo',...opts});
export const fmtHM=(d:any)=>brt({hour:'2-digit',minute:'2-digit'}).format(new Date(d));
export const fmtHMS=(d:any)=>brt({hour:'2-digit',minute:'2-digit',second:'2-digit'}).format(new Date(d));
export const fmtDateTime=(d:any)=>brt({day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}).format(new Date(d));
export const fmtPrice=(p:number|null)=>p==null?'—':p.toFixed(p>=100?3:5);
export function fmtCountdown(ms:number){const s=Math.max(0,Math.floor(ms/1000));return `${String(Math.floor(s/60)).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`;}
