// Pure presentation tests: original source identity and session/configuration matching.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const ts=require('../frontend-src/node_modules/typescript');
const source=fs.readFileSync(path.join(__dirname,'../frontend-src/src/lib/analysisSelection.ts'),'utf8');
const js=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
const output={};new Function('exports',js)(output);const {matchingAnalysis}=output;
const native={deriv:['EUR/USD','GBP/USD','USD/JPY'],binance:['BTC/USDT','ETH/USDT','SOL/USDT'],coinbase:['BTC/USD','ETH/USD']};
const assets=Object.values(native).flat(),asset_sources=Object.fromEntries(Object.entries(native).flatMap(([key,rows])=>rows.map(asset=>[asset,key])));
const analysis=(key,at,session='run')=>({id:key+at,source_key:key,scan_id:session,created_at:at,finished_at:at+1,
 assets:native[key],timeframe:'M5',duration_seconds:300,strategy:'scan_all',preview:true,
 asset_previews:native[key].map(asset=>({asset,source_key:key,status:'no_setup',reason:'Synthetic '+at}))});
const state={source_mode:'automatic',provider:'binance',asset_sources,scan:{id:'run'},
 settings:{assets,timeframe:'M5',duration_seconds:300,strategy:'scan_all'},
 analyses:[analysis('deriv',900,'other-session'),analysis('deriv',100),analysis('binance',101),analysis('coinbase',102)]};
const full=matchingAnalysis(state);
assert.equal(full.asset_previews.length,8);assert.match(full.message,/3\/3/);
for(const row of full.asset_previews){assert.equal(row.source_key,asset_sources[row.asset]);assert.equal(row.status,'no_setup');}
assert.equal(full.asset_previews.find(r=>r.asset==='EUR/USD').reason,'Synthetic 100');
const partial=matchingAnalysis({...state,analyses:[analysis('deriv',100)]});
assert.equal(partial.asset_previews.filter(r=>r.status==='waiting').length,5);assert.match(partial.message,/1\/3/);
assert.equal(partial.asset_previews.find(r=>r.asset==='BTC/USD').source_key,'coinbase');
assert.equal(matchingAnalysis({...state,scan:{id:'new-session'}}),null);
assert.equal(matchingAnalysis({...state,settings:{...state.settings,timeframe:'M1'}}),null);
assert.equal(matchingAnalysis({...state,settings:{...state.settings,duration_seconds:60}}),null);
assert.equal(matchingAnalysis({...state,settings:{...state.settings,assets:['BTC/USD']}}),null);
const manual=matchingAnalysis({...state,source_mode:'manual',provider:'deriv',settings:{...state.settings,assets:native.deriv}});
assert.equal(manual.source_key,'deriv');
console.log('Frontend presentation: all sources, partial progress, original native pairs, current session/configuration and legacy mode passed.');
