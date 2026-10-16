// Software/UI fixtures only. No real market data or Telegram messages.
const {spawn}=require('node:child_process'),assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const pw=require(process.env.PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const root=path.resolve(__dirname,'..'),out=process.env.AURORA_TEST_ARTIFACT_DIR||path.join(root,'validation',require('./package.json').version,'screenshots');fs.mkdirSync(out,{recursive:true});
 const server=spawn(process.env.AURORA_TEST_PYTHON||'python',['-m','uvicorn','tests.browser_setups_server:app','--host','127.0.0.1','--port','8125'],{cwd:root,stdio:['ignore','ignore','pipe']});let browser;
 try{
  await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('startup timeout')),15000);server.stderr.on('data',d=>{if(d.toString().includes('Application startup complete')){clearTimeout(timer);resolve()}})});
  browser=await pw.chromium.launch({executablePath:process.env.CHROMIUM_PATH,args:['--no-sandbox','--disable-gpu','--no-zygote']});
  const page=await browser.newPage({viewport:{width:1366,height:1000}}),errors=[],requests=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>requests.push(r.url()));
  await page.request.post('http://127.0.0.1:8125/_test/seed_setups');
  await page.goto('http://127.0.0.1:8125/aprendizado');const board=page.getByTestId('winning-setups'),rows=board.getByTestId('winning-setup-row');
  await rows.first().getByText('35 WIN',{exact:true}).waitFor();assert.equal(await rows.count(),5);
  assert((await rows.first().innerText()).includes('CCI(14)'));assert((await rows.first().innerText()).includes('70% de vitórias'));
  assert((await rows.nth(1).innerText()).includes('Coinbase'));assert((await rows.nth(1).innerText()).includes('80% de vitórias'));
  assert((await rows.nth(3).innerText()).includes('Amostra pequena'));
  await board.getByRole('button',{name:'Mostrar até 10',exact:true}).click();await page.waitForFunction(()=>document.querySelectorAll('[data-testid=winning-setup-row]').length===6);
  await board.getByRole('button',{name:'Mostrar 5',exact:true}).click();await page.waitForFunction(()=>document.querySelectorAll('[data-testid=winning-setup-row]').length===5);
  await rows.first().getByRole('button',{name:/Detalhes/}).click();await rows.first().getByText(/Fonte original: deriv:test/).waitFor();
  for(const width of [1366,390,360]){await page.setViewportSize({width,height:950});await board.scrollIntoViewIfNeeded();assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await board.screenshot({path:path.join(out,'winning-setups-'+width+'.png')})}
  // A delayed response for the former source must not replace the new one.
  let held,release;const captured=new Promise(r=>held=r),gate=new Promise(r=>release=r);
  const slow=async route=>{const url=new URL(route.request().url());if(url.searchParams.get('source')==='deriv'){held();await gate;try{await route.continue()}catch{}}else await route.continue()};
  await page.route('**/api/validation/setups?*',slow);
  await board.getByLabel('Fonte dos setups',{exact:true}).selectOption('deriv');await captured;
  await board.getByLabel('Fonte dos setups',{exact:true}).selectOption('coinbase');await rows.first().getByText('32 WIN',{exact:true}).waitFor();release();
  await page.unroute('**/api/validation/setups?*',slow);assert.equal(await rows.count(),1);assert((await rows.first().innerText()).includes('Coinbase'));
  await board.getByLabel('Resultados dos setups',{exact:true}).selectOption('manual');await rows.first().getByText('1 WIN',{exact:true}).waitFor();assert((await rows.first().innerText()).includes('Corretora teste'));
  await board.getByLabel('Resultados dos setups',{exact:true}).selectOption('bot');await rows.first().getByText('35 WIN',{exact:true}).waitFor();
  await board.getByText('Filtrar ativo, períodos e versão',{exact:true}).click();await board.getByLabel('Versão dos setups',{exact:true}).selectOption('old-test-version');await rows.first().getByText('1 WIN',{exact:true}).waitFor();assert.equal(await rows.count(),1);
  await board.getByLabel('Versão dos setups',{exact:true}).selectOption('current');await rows.first().getByText('35 WIN',{exact:true}).waitFor();
  assert((await board.innerText()).includes('sem preços/horários comprovados'));
  // Same-filter refresh failure preserves and identifies the last result.
  const unavailable=r=>r.fulfill({status:503,json:{detail:'Ranking temporariamente indisponível'}});
  await page.route('**/api/validation/setups?*',unavailable);await page.request.post('http://127.0.0.1:8125/_test/add_setup_win');
  await board.getByRole('alert').filter({hasText:'Últimos resultados preservados'}).waitFor();assert((await rows.first().innerText()).includes('35 WIN'));
  await page.unroute('**/api/validation/setups?*',unavailable);await page.reload();await rows.first().getByText('36 WIN',{exact:true}).waitFor();
  const state=await(await page.request.get('http://127.0.0.1:8125/api/state')).json();assert.equal(state.scan.active,false);assert.deepEqual(errors,[]);
  assert(!requests.some(url=>url.endsWith('/api/validation')));
  console.log('PASS setup WIN ranking by count, small-sample warning, five/ten limit, source/manual/version isolation, stale response and API failure, restart refresh, desktop/mobile and no JavaScript errors');
 }finally{if(browser)await browser.close();server.kill()}
})().catch(e=>{console.error(e);process.exitCode=1});
