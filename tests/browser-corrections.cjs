// Software fixtures only. No real market queries, messages or operations.
const {spawn}=require('node:child_process'),assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const pw=require(process.env.PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const root=path.resolve(__dirname,'..'),out=process.env.AURORA_TEST_ARTIFACT_DIR||path.join(root,'validation',require('./package.json').version,'screenshots');fs.mkdirSync(out,{recursive:true});
 const server=spawn(process.env.AURORA_TEST_PYTHON||'python',['-m','uvicorn','tests.browser_corrections_server:app','--host','127.0.0.1','--port','8127'],{cwd:root,stdio:['ignore','ignore','pipe']});let browser;
 try{
  await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('startup timeout')),15000);server.stderr.on('data',d=>{if(d.toString().includes('Application startup complete')){clearTimeout(timer);resolve()}})});
  browser=await pw.chromium.launch({executablePath:process.env.CHROMIUM_PATH,args:['--no-sandbox','--disable-gpu','--no-zygote']});
  const page=await browser.newPage({viewport:{width:1366,height:900}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.request.post('http://127.0.0.1:8127/_test/seed_corrections');await page.goto('http://127.0.0.1:8127');
  await page.getByRole('alert').filter({hasText:'Telegram confirmado com atraso'}).waitFor();
  await page.getByRole('link',{name:'Aprendizado',exact:true}).click();
  const totals=page.locator('[aria-label="Resultados dos sinais"]');assert((await totals.innerText()).includes('Vitórias: 1'));assert((await totals.innerText()).includes('Sem comprovação: 1'));
  const board=page.getByTestId('winning-setups');await board.getByLabel('Resultados dos setups').selectOption('manual');
  await board.getByText('Filtrar ativo, períodos e versão',{exact:true}).click();
  await board.getByLabel('Expiração dos setups').selectOption('63.5');await board.getByText('1 WIN',{exact:true}).waitFor();
  assert((await board.innerText()).includes('63.5s'));
  for(const width of [1366,390,360]){await page.setViewportSize({width,height:950});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(out,'audit-corrections-'+width+'.png'),fullPage:true})}
  await page.goto('http://127.0.0.1:8127/validacao');await page.getByRole('tab',{name:'Sinais do bot',exact:true}).click();
  await page.getByTestId('metric-group').first().getByText('Ver detalhes',{exact:true}).click();await page.getByText('Indisponíveis entre encerrados',{exact:true}).waitFor();
  assert((await page.locator('[aria-label="Combinações de validação"]').innerText()).includes('50%'));
  assert.deepEqual(errors,[]);console.log('PASS late ACK warning, proof-only panel totals, custom manual duration filtering, verified unavailable metrics, desktop/mobile and no JS errors');
 }finally{if(browser)await browser.close();server.kill()}
})().catch(e=>{console.error(e);process.exitCode=1});
