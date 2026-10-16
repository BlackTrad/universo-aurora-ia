// Local synthetic service: no external market, Telegram or Gemini actions.
const {spawn}=require('node:child_process'),assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const pw=require(process.env.PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const root=path.resolve(__dirname,'..'),out=process.env.AURORA_TEST_ARTIFACT_DIR||path.join(root,'validation',require('./package.json').version,'screenshots');fs.mkdirSync(out,{recursive:true});
 const server=spawn(process.env.AURORA_TEST_PYTHON||'python',['-m','uvicorn','tests.browser_release_server:app','--host','127.0.0.1','--port','8116'],{cwd:root,stdio:['ignore','ignore','pipe']});let browser;
 try{
  await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('startup')),15000);server.stderr.on('data',d=>{if(d.toString().includes('Application startup complete')){clearTimeout(timer);resolve()}})});
  browser=await pw.chromium.launch({executablePath:process.env.CHROMIUM_PATH,args:['--no-sandbox','--disable-gpu','--no-zygote']});
  const page=await browser.newPage({viewport:{width:1366,height:920}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  let offline=false,clearCalls=0;
  await page.route('**/api/state',async route=>{if(offline)return route.fulfill({status:503,json:{detail:'Controlled disconnect'}});const res=await route.fetch(),data=await res.json();return route.fulfill({json:{...data,feed:{...data.feed,status:'disponível',detail:'Synthetic status'}}})});
  // This case checks the availability label. Keep the chart clock controlled too,
  // independently of the trading fixture's fixed next-candle entry window.
  await page.route('**/api/chart?*',async route=>{const res=await route.fetch(),data=await res.json();return route.fulfill({json:{...data,status:'live',data_at:Date.now()/1000}})});
  page.on('request',r=>{if(r.url().endsWith('/api/history/clear')&&r.method()==='POST')clearCalls++});
  await page.request.post('http://127.0.0.1:8116/_test/public_sources');
  await page.goto('http://127.0.0.1:8116');
  const status=page.getByRole('status',{name:'Disponibilidade da fonte de dados'});
  await status.filter({hasText:/disponível|Atualizado/}).waitFor();
  assert.equal(await page.locator('main').getByText('AURORA',{exact:true}).count(),0);
  const bounds=await Promise.all([status.boundingBox(),page.locator('header summary').boundingBox()]);
  assert(bounds[0].x>bounds[1].x+bounds[1].width);assert(Math.abs(bounds[0].y-bounds[1].y)<15);
  await page.screenshot({path:path.join(out,'header-desktop.png')});
  offline=true;await status.filter({hasText:'Sem conexão'}).waitFor();offline=false;await status.filter({hasText:/disponível|Atualizado/}).waitFor();
  for(const width of [360,390,768]){
   await page.setViewportSize({width,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   const header=await page.locator('header').boundingBox(),badge=await status.boundingBox();assert(badge.x+badge.width<=width&&badge.y<header.y+header.height);
   if(width===390)await page.screenshot({path:path.join(out,'header-mobile.png')});
  }
  await page.setViewportSize({width:1366,height:920});
  const first=(await(await page.request.post('http://127.0.0.1:8116/_test/emit_catalogue_signal?source=coinbase')).json()).signal;
  await page.request.post('http://127.0.0.1:8116/_test/resolve_catalogue_signal?sid='+first.id);
  // Resolving the next fixture advances the shared clock. Keep this one pending
  // beyond that advance so the real settlement worker cannot legitimately close it.
  const pending=(await(await page.request.post('http://127.0.0.1:8116/_test/emit_catalogue_signal?source=coinbase&entry_offset_seconds=3600')).json()).signal;
  await page.goto('http://127.0.0.1:8116/historico');
  await page.getByLabel('Período da limpeza').waitFor();
  const day=new Intl.DateTimeFormat('en-CA',{timeZone:'America/Sao_Paulo',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(first.entry_at*1000));
  await page.getByLabel('Data da limpeza').fill(day);
  await page.getByRole('button',{name:'Revisar limpeza',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'Confirmar limpeza do histórico'});await dialog.waitFor();
  await dialog.getByRole('button',{name:'Cancelar',exact:true}).click();assert.equal(clearCalls,0);
  await page.getByRole('button',{name:'Revisar limpeza',exact:true}).click();
  await dialog.getByRole('button',{name:'Confirmar limpeza',exact:true}).dblclick();
  await page.getByRole('status').filter({hasText:'1 operação(ões) removida(s)'}).waitFor();assert.equal(clearCalls,1);
  let rows=await(await page.request.get('http://127.0.0.1:8116/api/history')).json();assert.equal(rows.total,1);assert.equal(rows.items[0].id,pending.id);
  assert.equal((await(await page.request.get('http://127.0.0.1:8116/api/signals/'+first.id)).json()).signal.result,'WIN');
  const second=(await(await page.request.post('http://127.0.0.1:8116/_test/emit_catalogue_signal?source=binance')).json()).signal;
  await page.request.post('http://127.0.0.1:8116/_test/resolve_catalogue_signal?sid='+second.id);
  await page.getByLabel('Período da limpeza').selectOption('month');await page.getByLabel('Data da limpeza').fill(day.slice(0,7));
  await page.getByRole('button',{name:'Revisar limpeza',exact:true}).click();await dialog.getByRole('button',{name:'Confirmar limpeza',exact:true}).click();
  await page.getByRole('status').filter({hasText:'1 operação(ões) removida(s)'}).waitFor();
  await page.getByLabel('Período da limpeza').selectOption('all');
  await page.getByRole('button',{name:'Revisar limpeza',exact:true}).click();await dialog.getByRole('button',{name:'Confirmar limpeza',exact:true}).click();
  await page.getByRole('status').filter({hasText:'0 operação(ões) removida(s)'}).waitFor();
  rows=await(await page.request.get('http://127.0.0.1:8116/api/history')).json();assert.equal(rows.total,1);assert.equal(rows.items[0].id,pending.id);
  const report=await(await page.request.get('http://127.0.0.1:8116/api/validation')).json();assert.equal(report.counts.generated,3);assert.equal(report.history_visibility.archived,2);
  await page.screenshot({path:path.join(out,'history-desktop.png')});
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(out,'history-mobile.png')});
  for(const route of ['/validacao','/conexoes','/noticias']){
   await page.goto('http://127.0.0.1:8116'+route);await status.filter({hasText:/disponível|Atualizado/}).waitFor();assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  }
  assert.deepEqual(errors,[]);
  console.log('PASS availability beside compact navigation, duplicate name removed, offline/recovery, desktop/mobile; clear day/month/all, cancel, double click, preserve pending/originals/validation and all page navigation');
 }finally{if(browser)await browser.close();server.kill()}
})().catch(e=>{console.error(e);process.exitCode=1});
