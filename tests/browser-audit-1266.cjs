// Controlled local frontend regressions, no messages to Telegram or real trades.
const {spawn}=require('node:child_process'),assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const pw=require(process.env.PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const root=process.env.AURORA_TEST_ROOT||path.resolve(__dirname,'..'),url='http://127.0.0.1:8130';
 const out=process.env.AURORA_TEST_ARTIFACT_DIR||path.join(root,'validation',require('./package.json').version,'screenshots');fs.mkdirSync(out,{recursive:true});
 const server=spawn(process.env.AURORA_TEST_PYTHON||'python',['-m','uvicorn','tests.browser_audit_1266_server:app','--host','127.0.0.1','--port','8130'],{cwd:root,stdio:['ignore','ignore','pipe']});let browser;
 try{
  await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('startup timeout')),15000);server.stderr.on('data',d=>{if(d.toString().includes('Application startup complete')){clearTimeout(timer);resolve()}})});
  browser=await pw.chromium.launch({executablePath:process.env.CHROMIUM_PATH,args:['--no-sandbox','--disable-gpu','--no-zygote']});
  const page=await browser.newPage({viewport:{width:1366,height:950}}),errors=[],requests=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.url().includes('/api/chart'))requests.push(r.url())});
  await page.route('**/api/news',r=>r.fulfill({json:{status:'unavailable',items:[],updated_at:null}}));
  await page.request.post(url+'/_test/public_sources');
  const base={provider:'deriv',strategy:'scan_all',assets:['EUR/USD'],timeframe:'M5',duration_seconds:300};
  assert((await page.request.put(url+'/api/settings',{data:base})).ok());await page.goto(url);
  await page.getByRole('button',{name:'Iniciar',exact:true}).waitFor();
  let state=await(await page.request.get(url+'/api/state')).json();assert.equal(state.scan.active,false);assert.equal(state.busy,false);
  // A provider selected elsewhere must normalize an unsupported display period.
  await Promise.all([page.waitForResponse(r=>r.url().includes('/api/chart?')&&r.url().includes('timeframe=M2')&&r.status()===200),page.getByRole('button',{name:'M2',exact:true}).click()]);
  let cutoff=requests.length;
  assert((await page.request.put(url+'/api/connection',{data:{provider:'binance'}})).ok());
  await page.waitForResponse(r=>r.url().includes('/api/chart?')&&r.url().includes('source=binance')&&r.url().includes('timeframe=M5')&&r.status()===200);
  assert(!requests.slice(cutoff).some(u=>u.includes('source=binance')&&u.includes('timeframe=M2')));
  assert.equal(await page.getByRole('button',{name:'M2',exact:true}).count(),0);
  assert((await page.request.put(url+'/api/connection',{data:{provider:'deriv'}})).ok());
  await page.getByRole('button',{name:'M3',exact:true}).waitFor();await Promise.all([page.waitForResponse(r=>r.url().includes('/api/chart?')&&r.url().includes('timeframe=M3')&&r.status()===200),page.getByRole('button',{name:'M3',exact:true}).click()]);
  cutoff=requests.length;
  assert((await page.request.put(url+'/api/connection',{data:{provider:'coinbase'}})).ok());
  await page.waitForResponse(r=>r.url().includes('/api/chart?')&&r.url().includes('source=coinbase')&&r.url().includes('timeframe=M5')&&r.status()===200);
  assert(!requests.slice(cutoff).some(u=>u.includes('source=coinbase')&&u.includes('timeframe=M3')));
  assert((await page.request.put(url+'/api/connection',{data:{provider:'deriv'}})).ok());
  assert((await page.request.put(url+'/api/settings',{data:base})).ok());
  await page.getByRole('button',{name:'M2',exact:true}).waitFor();
  await page.request.post(url+'/_test/slow_preview');
  await page.getByRole('button',{name:'Analisar agora',exact:true}).click();
  const stop=page.getByRole('button',{name:'Parar',exact:true});await stop.waitFor();assert(await stop.isEnabled());
  await page.getByRole('heading',{name:'Prévia em andamento',exact:true}).waitFor();
  await page.getByText(/Prévia técnica sem envio. Você pode interromper em Parar./).waitFor();
  for(const width of [1366,390,360]){
   await page.setViewportSize({width,height:950});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await page.screenshot({path:path.join(out,'audit-1266-preview-'+width+'.png'),fullPage:true});
  }
  await stop.click();await page.getByRole('button',{name:'Iniciar',exact:true}).waitFor();
  state=await(await page.request.get(url+'/api/state')).json();assert.equal(state.analysis_active,false);assert.equal(state.scan.active,false);assert.equal(state.analyses[0].status,'interrompida');
  const audit=await(await page.request.get(url+'/_test/audit_status')).json();assert.equal(audit.signals,0);assert.equal(audit.telegram_calls,0);
  await page.request.post(url+'/_test/restore_feed');
  // Same page remains usable after cancellation; rapid clicks make one preview.
  await page.evaluate(()=>{const b=[...document.querySelectorAll('button')].find(e=>e.textContent==='Analisar agora');b.click();b.click()});
  await page.waitForFunction(async()=>{const s=await(await fetch('/api/state')).json();return !s.busy&&s.analyses.length===2});
  state=await(await page.request.get(url+'/api/state')).json();assert.equal(state.analyses.length,2);assert.equal(state.signals.length,0);assert.equal(state.scan.active,false);
  assert.deepEqual(errors,[]);
  console.log('PASS idle startup; Deriv M2/M3 to Binance/Coinbase timeframe compatibility; interrupt manual preview; no emission; recovery and double-click guard; desktop 1366/mobile 390/360 without overflow or JavaScript errors');
 }finally{if(browser)await browser.close();server.kill()}
})().catch(e=>{console.error(e);process.exitCode=1});
