// Real local UI with synthetic providers; no actual market, account or Telegram.
const {spawn}=require('node:child_process'),assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const pw=require(process.env.PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const root=process.env.AURORA_TEST_ROOT||path.resolve(__dirname,'..'),url='http://127.0.0.1:8141';
 const out=process.env.AURORA_TEST_ARTIFACT_DIR||path.join(root,'validation',require('./package.json').version,'screenshots');fs.mkdirSync(out,{recursive:true});
 const server=spawn(process.env.AURORA_TEST_PYTHON||'python',['-m','uvicorn','tests.browser_release_server:app','--host','127.0.0.1','--port','8141'],{cwd:root,stdio:['ignore','ignore','pipe']});let browser;
 try{
  await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('startup timeout')),15000);server.once('exit',code=>{clearTimeout(timer);reject(Error('server exit '+code))});server.stderr.on('data',d=>{if(d.toString().includes('Application startup complete')){clearTimeout(timer);resolve()}})});
  browser=await pw.chromium.launch({executablePath:process.env.CHROMIUM_PATH,args:['--no-sandbox','--disable-gpu','--no-zygote']});
  const page=await browser.newPage({viewport:{width:1440,height:950},timezoneId:'America/Sao_Paulo'}),errors=[],posts=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()==='POST'&&/\/api\/(scan\/start|analyze)$/.test(r.url()))posts.push(r.url())});
  await page.route('**/api/news',r=>r.fulfill({json:{status:'unavailable',items:[],updated_at:null}}));
  const state=async()=>(await page.request.get(url+'/api/state')).json();
  assert((await page.request.put(url+'/api/settings',{data:{provider:'deriv',assets:['EUR/USD'],timeframe:'M5',duration_seconds:300}})).ok());
  const before=(await state()).settings;
  await page.goto(url+'/configuracoes');await page.getByLabel('Versão do projeto').filter({hasText:'1.26.13'}).waitFor();
  await page.getByText('Estratégias do manual · Price Action para opções binárias',{exact:true}).click();
  await page.getByText('39 padrões · 1/5',{exact:true}).waitFor();
  await page.getByLabel('Pesquisar estratégias do manual').fill('50%');await page.getByText('Rompimento de 50% e reteste',{exact:true}).waitFor();
  await page.getByLabel('Pesquisar estratégias do manual').fill('OCO');await page.getByText('OCO / OCO invertido com reteste',{exact:true}).waitFor();
  await page.getByLabel('Pesquisar estratégias do manual').fill('');
  assert.equal(await page.getByText(/Soros|simulador/).count(),0);
  assert.deepEqual((await state()).settings,before);assert.equal((await state()).scan.active,false);assert.equal(posts.length,0);
  for(const width of [360,768,1440]){await page.setViewportSize({width,height:950});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(out,'manual-settings-'+width+'.png'),fullPage:true})}
  await page.setViewportSize({width:1440,height:950});await page.goto(url);
  await page.getByRole('button',{name:'Iniciar',exact:true}).click();await page.getByText('Nova oportunidade identificada',{exact:false}).waitFor();
  await page.locator('[data-level-id="manual_region"]').waitFor();
  const snapshot=await state();assert(snapshot.signals.every(s=>s.manual&&s.strategy_resolved.startsWith('pa_')));
  assert(snapshot.signals.every(s=>s.entry_at<s.setup_contract.entry_window_end));
  assert(snapshot.signals.every(s=>s.chart_annotations.zones[0].id==='manual_region'));
  // Wait for the existing server-clock synchronization before capturing the
  // synthetic signal; the fixture intentionally runs near an M5 opening.
  await page.getByText(/Entrar em:/).waitFor();
  await page.getByRole('button',{name:'Parar',exact:true}).click();
  await page.screenshot({path:path.join(out,'AURORA-1.26.13-frontend.png'),fullPage:true});
  assert.deepEqual(errors,[]);
  console.log('PASS manual catalog/search/pagination, no Soros/read-only settings, explicit timing, native default Price Action pipeline, original region annotations, desktop/responsive layout and no JS errors');
 }finally{if(browser)await browser.close();server.kill()}
})().catch(e=>{console.error(e);process.exitCode=1});
