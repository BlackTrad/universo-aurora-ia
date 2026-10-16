// Synthetic arithmetic and UI isolation. No market or Telegram service is called.
const {spawn}=require('node:child_process'),assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const pw=require(process.env.PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const root=path.resolve(__dirname,'..'),out=process.env.AURORA_TEST_ARTIFACT_DIR||path.join(root,'validation',require('./package.json').version,'screenshots');fs.mkdirSync(out,{recursive:true});
 const server=spawn(process.env.AURORA_TEST_PYTHON||'python',['-m','uvicorn','tests.browser_evolution_server:app','--host','127.0.0.1','--port','8124'],{cwd:root,stdio:['ignore','ignore','pipe']});let browser;
 try{
  await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('startup timeout')),15000);server.stderr.on('data',d=>{if(d.toString().includes('Application startup complete')){clearTimeout(timer);resolve()}})});
  browser=await pw.chromium.launch({executablePath:process.env.CHROMIUM_PATH,args:['--no-sandbox','--disable-gpu','--no-zygote']});
  const page=await browser.newPage({viewport:{width:1366,height:1000}}),errors=[],requests=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>requests.push(r.url()));
  await page.route('**/api/news',r=>r.fulfill({json:{status:'unavailable',items:[],updated_at:null}}));
  const original=(await(await page.request.post('http://127.0.0.1:8124/_test/seed_evolution')).json()).signal;
  assert.equal(original.observed_win_rate.rate_pct,60);assert.equal(original.score,95);
  await page.goto('http://127.0.0.1:8124');await page.getByText('Explicação do motor',{exact:true}).click();const badge=page.locator('[data-testid=observed-rate]');await badge.filter({hasText:'60%'}).waitFor();
  assert((await badge.innerText()).includes('30 resultados'));assert(!await page.getByText(/Pontuação técnica:/).count());
  assert(!await page.getByText(/70 pts/).isVisible());
  for(const width of [1366,390,360]){await page.setViewportSize({width,height:900});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(out,'victory-rate-'+width+'.png'),fullPage:true})}
  await page.goto('http://127.0.0.1:8124/validacao');await page.getByRole('tab',{name:'Experimentos',exact:true}).click();
  const toggle=page.getByRole('button',{name:'Habilitar coleta experimental',exact:true});await toggle.waitFor();
  let writes=0;page.on('request',r=>{if(r.url().includes('/validation/observation')&&r.method()==='PUT')writes++});
  await toggle.evaluate(el=>{el.click();el.click()});await page.getByRole('button',{name:'Pausar coleta experimental',exact:true}).waitFor();assert.equal(writes,1);
  let state=await(await page.request.get('http://127.0.0.1:8124/api/state')).json();assert.equal(state.scan.active,false);assert.equal(state.signals.length,20);assert.equal(state.settings.selection_observation,true);
  await page.getByRole('button',{name:'Pausar coleta experimental',exact:true}).click();await page.getByRole('button',{name:'Habilitar coleta experimental',exact:true}).waitFor();
  await page.reload();await page.getByRole('tab',{name:'Experimentos',exact:true}).click();await page.getByRole('button',{name:'Habilitar coleta experimental',exact:true}).waitFor();
  state=await(await page.request.get('http://127.0.0.1:8124/api/state')).json();assert.equal(state.settings.selection_observation,false);assert.equal(state.scan.active,false);
  await page.getByRole('tab',{name:'Sinais do bot',exact:true}).click();await page.locator('[data-testid=metric-group]').waitFor();
  assert(requests.some(x=>x.includes('/validation/groups?')));assert(!requests.some(x=>x.endsWith('/api/validation')));
  await page.request.post('http://127.0.0.1:8124/_test/resolve_evolution');
  const saved=(await(await page.request.get('http://127.0.0.1:8124/api/signals/'+original.id)).json()).signal;
  assert.deepEqual(saved.observed_win_rate,original.observed_win_rate);assert.deepEqual(saved.engine_explanation,original.engine_explanation);
  await page.goto('http://127.0.0.1:8124/historico');await page.locator('tr').filter({hasText:'Conferir operação'}).first().locator('[data-testid=observed-rate]').filter({hasText:'60%'}).waitFor();
  assert.deepEqual(errors,[]);assert(!requests.some(x=>x.includes('generativelanguage')));
  console.log('PASS observed wins 60% from 18/30 instead of score 95, immutable snapshot, paged API, experimental double-click/pause/reload isolation, responsive and no JavaScript errors');
 }finally{if(browser)await browser.close();server.kill()}
})().catch(e=>{console.error(e);process.exitCode=1});
