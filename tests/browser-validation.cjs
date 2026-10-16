// Controlled local feeds only; no real Telegram/Gemini calls.
const {spawn}=require('node:child_process'),assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const pw=require(process.env.PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const root=path.resolve(__dirname,'..');
 const server=spawn(process.env.AURORA_TEST_PYTHON||'python',['-m','uvicorn','tests.browser_release_server:app','--host','127.0.0.1','--port','8115'],{cwd:root,stdio:['ignore','ignore','pipe']});let browser;
 try{
  await new Promise((resolve,reject)=>{const t=setTimeout(()=>reject(Error('startup')),15000);server.stderr.on('data',d=>{if(d.toString().includes('Application startup complete')){clearTimeout(t);resolve()}})});
  browser=await pw.chromium.launch({executablePath:process.env.CHROMIUM_PATH,args:['--no-sandbox','--disable-gpu','--no-zygote']});
  const page=await browser.newPage({viewport:{width:1366,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.request.post('http://127.0.0.1:8115/_test/public_sources');await page.goto('http://127.0.0.1:8115');
  await page.getByRole('button',{name:/BTC\/USD Coinbase/}).waitFor();
  await page.getByRole('button',{name:/BTC\/USDT Binance/}).waitFor();
  const before=(await(await page.request.get('http://127.0.0.1:8115/api/state')).json()).settings;
  await page.getByRole('button',{name:/BTC\/USDT Binance/}).click();
  await page.getByText('Binance · Brasília (UTC−3)',{exact:false}).waitFor();
  assert.deepEqual((await(await page.request.get('http://127.0.0.1:8115/api/state')).json()).settings,before);
  await page.getByLabel('Pesquisar ativo').fill('Bitcoin');
  await page.waitForFunction(()=>document.querySelector('.asset-grid')?.querySelectorAll('button').length===2);
  await page.getByLabel('Pesquisar ativo').fill('BTC/USD');
  await page.waitForFunction(()=>document.querySelector('.asset-grid')?.querySelectorAll('button').length===1);
  await page.getByRole('button',{name:/BTC\/USD Coinbase/}).click();
  await page.getByText('Coinbase · Brasília (UTC−3)',{exact:false}).waitFor();
  await page.getByLabel('Pesquisar ativo').fill('');await page.getByLabel('Pesquisar ativo').fill('');
  await page.waitForFunction(()=>document.querySelector('.asset-grid')?.querySelectorAll('button').length===6);
  await page.getByRole('button',{name:'Próxima',exact:true}).click();
  await page.waitForFunction(()=>document.body.textContent.includes('Página 2/2'));
  await page.getByRole('button',{name:'Anterior',exact:true}).click();
  // A stale chart response is held while a new signal changes source+symbol+timeframe.
  let released,held;const captured=new Promise(resolve=>held=resolve);
  await page.route('**/api/chart?**',async route=>{const u=new URL(route.request().url());if(u.searchParams.get('source')!=='binance')return route.continue();const response=await route.fetch();held();await new Promise(resolve=>released=resolve);try{await route.fulfill({response})}catch{}});
  await page.getByRole('button',{name:/BTC\/USDT Binance/}).click();await captured;
  const emitted=(await(await page.request.post('http://127.0.0.1:8115/_test/emit_catalogue_signal?source=coinbase&timeframe=M5')).json()).signal;
  await page.waitForFunction(()=>document.querySelector('label')?.textContent!==''&&document.body.textContent.includes('Visualização: Coinbase'));
  await page.getByText('Coinbase · Brasília (UTC−3)',{exact:false}).waitFor();released();await page.waitForTimeout(500);
  assert(await page.getByText('Coinbase · Brasília (UTC−3)',{exact:false}).count());
  await page.getByRole('heading',{name:'BTC/USD',exact:false}).waitFor();
  await page.request.post('http://127.0.0.1:8115/_test/resolve_catalogue_signal?sid='+emitted.id);
  await page.goto('http://127.0.0.1:8115/historico');
  await page.getByLabel('Dia do histórico').waitFor();
  await page.getByRole('link',{name:'Salvar histórico deste dia'}).waitFor();
  await page.getByRole('link',{name:'Conferir operação',exact:true}).click();
  await page.getByRole('heading',{name:'Validação dos sinais',exact:true}).waitFor();
  assert.equal(await page.getByLabel('ID do sinal',{exact:true}).inputValue(),emitted.id);
  await page.getByLabel('Corretora',{exact:true}).fill('Corretora declarada');
  await page.getByLabel('Resultado da conferência',{exact:true}).selectOption('LOSS');
  await page.getByRole('button',{name:'Registrar conferência',exact:true}).dblclick();
  await page.getByRole('status').filter({hasText:'Conferência registrada separadamente'}).waitFor();
  const detail=await(await page.request.get('http://127.0.0.1:8115/api/signals/'+emitted.id)).json();
  assert.equal(detail.signal.result,'WIN');assert.equal(detail.manual_confirmations.length,1);assert.equal(detail.manual_confirmations[0].result,'LOSS');
  const out=process.env.AURORA_TEST_ARTIFACT_DIR||path.join(root,'validation',require('./package.json').version,'screenshots');fs.mkdirSync(out,{recursive:true});
  await page.screenshot({path:path.join(out,'validation-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:path.join(out,'validation-mobile.png'),fullPage:true});
  await page.goto('http://127.0.0.1:8115');assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:path.join(out,'catalogue-mobile.png'),fullPage:true});
  assert.deepEqual(errors,[]);console.log('PASS catalogue sources/search/pages, source isolation, automatic signal chart and stale-response rejection, daily history, manual result isolation, double click, desktop/mobile');
 }finally{if(browser)await browser.close();server.kill()}
})().catch(e=>{console.error(e);process.exitCode=1});
