// Native application with synthetic timestamped prices. No live trade or Telegram.
const {spawn}=require('node:child_process'),assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const pw=require(process.env.PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const root=process.env.AURORA_TEST_ROOT||path.resolve(__dirname,'..'),url='http://127.0.0.1:8142';
 const out=process.env.AURORA_TEST_ARTIFACT_DIR||path.join(root,'validation','1.26.13','screenshots');fs.mkdirSync(out,{recursive:true});
 const server=spawn(process.env.AURORA_TEST_PYTHON||'python',['-m','uvicorn','tests.browser_book_server:app','--host','127.0.0.1','--port','8142'],{cwd:root,stdio:['ignore','ignore','pipe']});let browser;
 try{
  await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('startup')),15000);server.once('exit',code=>{clearTimeout(timer);reject(Error('server exit '+code))});server.stderr.on('data',d=>{if(d.toString().includes('Application startup complete')){clearTimeout(timer);resolve()}})});
  browser=await pw.chromium.launch({executablePath:process.env.CHROMIUM_PATH,args:['--no-sandbox','--disable-gpu','--no-zygote']});
  const page=await browser.newPage({viewport:{width:1440,height:950},timezoneId:'America/Sao_Paulo'}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  const state=async()=>(await page.request.get(url+'/api/state')).json();
  assert((await page.request.put(url+'/api/settings',{data:{provider:'deriv',assets:['EUR/USD'],timeframe:'M5',duration_seconds:300}})).ok());
  await page.goto(url+'/configuracoes');await page.getByLabel('Versão do projeto').filter({hasText:'1.26.13'}).waitFor();
  await page.getByText('Estratégias do manual · Price Action para opções binárias',{exact:true}).click();
  await page.getByText('39 padrões · 1/5',{exact:true}).waitFor();
  await page.getByLabel('Pesquisar estratégias do manual').fill('Fluxo após Harami');await page.getByText('Fluxo após Harami',{exact:true}).waitFor();
  assert.equal(await page.getByText(/Soros|simulador/).count(),0);assert.equal((await state()).scan.active,false);
  assert.equal((await page.request.get(url+'/api/manual/management?capital=100&payout_pct=87')).status(),404);
  for(const width of [360,768,1440]){await page.setViewportSize({width,height:950});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(out,'book-settings-'+width+'.png'),fullPage:true})}
  await page.goto(url);await page.getByRole('button',{name:'Iniciar',exact:true}).click();
  await page.waitForFunction(async()=>{const s=await(await fetch('/api/state')).json();return s.intrabar.watching_assets===1&&s.intrabar.last_quotes['EUR/USD']?.code==='live_price_stale'});
  assert.equal((await state()).signals.length,0);
  await page.request.post(url+'/_test/book_mode?value=good');
  const created=await(await page.request.post(url+'/_test/poll_book')).json();assert.equal(created.signals.length,1);
  await page.getByText('Gatilho observado durante a vela · entrada programada sujeita à revalidação.',{exact:true}).waitFor();
  await page.locator('[data-level-id="manual_region"]').waitFor();await page.getByText(/Entrar em:/).waitFor();
  const s=(await state()).signals[0];assert.equal(s.manual_intrabar,true);assert.equal(s.live_observation.price,100.25);
  assert(s.entry_at<s.setup_contract.entry_window_end);assert.equal(s.manual_rules_version,'1.26.13-book-1');
  await page.getByRole('button',{name:'Parar',exact:true}).click();
  const stopped=await(await page.request.post(url+'/_test/poll_book')).json();assert.equal(stopped.price_calls,created.price_calls);assert.equal(stopped.signals.length,0);
  await page.screenshot({path:path.join(out,'AURORA-1.26.13-frontend.png'),fullPage:true});
  assert.deepEqual(errors,[]);
  console.log('PASS 39 book rules, no Soros, read-only catalog, stale live price blocks native scanner, authenticated fixture quote commits one intrabar signal, original annotation/time window, Stop ends new reads, responsive layouts and no JS errors');
 }finally{if(browser)await browser.close();server.kill()}
})().catch(e=>{console.error(e);process.exitCode=1});
