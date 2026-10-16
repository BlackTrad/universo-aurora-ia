// Display periods follow provider capabilities; operational settings stay separate.
export function chartTimeframeFor(provider:string,requested:string):string {
 const value=requested==='M1'?'1m':requested==='M5'?'5m':requested;
 return (provider==='deriv'?['1m','M2','M3','5m']:['1m','5m']).includes(value)?value:'5m';
}

// Navigate from diagnostics without changing the operational configuration.
export function chartLink(provider:string,asset:string,timeframe='M1'):string {
 return '/bot?'+new URLSearchParams({source:provider,asset,timeframe}).toString();
}
