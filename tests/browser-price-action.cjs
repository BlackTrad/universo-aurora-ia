// Synthetic records test the UI, never trading efficacy or Telegram delivery.
const {spawn}=require('node:child_process'),assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const pw=require(process.env.PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const root=path.resolve(__dirname,'..'),out=process.env.AURORA_TEST_ARTIFACT_DIR||path.join(root,'validation',require('./package.json').version,'screenshots');fs.mkdirSync(out,{recursive:true});
 const server=spawn(process.env.AURORA_TEST_PYTHON||'python',['-m','uvicorn','tests.browser_price_action_server:app','--host','127.0.0.1','--port','8137'],{cwd:root,stdio:['ignore','ignore','pipe']});let browser;
 try{
  await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('startup timeout')),15000);server.stderr.on('data',d=>{if(d.toString().includes('Application startup complete')){clearTimeout(timer);resolve()}})});
  browser=await pw.chromium.launch({executablePath:process.env.CHROMIUM_PATH,args:['--no-sandbox','--disable-gpu','--no-zygote']});
  const page=await browser.newPage({viewport:{width:1366,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.request.post('http://127.0.0.1:8137/_test/seed_price_action');
  await page.goto('http://127.0.0.1:8137/validacao');await page.getByRole('tab',{name:'Experimentos',exact:true}).click();
  const study=page.getByRole('region',{name:'Estudo de Price Action para opções binárias'});
  await study.getByText('Pullback estrutural',{exact:true}).waitFor();
  assert.equal(await study.locator('tbody tr').count(),4);
  await study.getByText(/Última decisão/).click();await study.getByText('CALL e PUT entre setups elegíveis; conflito bloqueado.',{exact:true}).waitFor();
  await page.getByText(/1 resultado\(s\) experimental\(is\) sem prova válida/).waitFor();
  await page.getByLabel('Seletor experimental',{exact:true}).selectOption('price_action_router');
  await page.waitForFunction(()=>document.querySelectorAll('[data-testid="metric-group"]').length===1);
  const metrics=page.getByRole('region',{name:'Combinações de validação'});
  await metrics.getByRole('button',{name:'Ver detalhes',exact:true}).click();await metrics.getByText('price_action_router / lateral',{exact:true}).waitFor();
  await metrics.getByRole('button',{name:'Limpar filtros',exact:true}).click();
  await page.waitForFunction(()=>document.querySelectorAll('[data-testid="metric-group"]').length===6);
  for(const width of [1366,390,360]){await page.setViewportSize({width,height:950});await study.scrollIntoViewIfNeeded();assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await study.screenshot({path:path.join(out,'price-action-'+width+'.png')})}
  await page.getByRole('tab',{name:'Sinais do bot',exact:true}).click();assert.equal(await page.getByLabel('Seletor experimental',{exact:true}).count(),0);
  const state=await(await page.request.get('http://127.0.0.1:8137/api/state')).json();assert.equal(state.scan.active,false);assert.equal(state.signals.length,0);assert.deepEqual(errors,[]);
  console.log('PASS four non-SMC observation setups, immutable conflict reasons, invalid price proof warning, selector filter and clear, bot isolation, desktop/mobile and no JavaScript errors');
 }finally{if(browser)await browser.close();server.kill()}
})().catch(e=>{console.error(e);process.exitCode=1});
