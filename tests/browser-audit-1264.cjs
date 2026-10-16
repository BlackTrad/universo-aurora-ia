// Controlled catalogue failure/recovery. No real quotes or Telegram messages.
const {spawn}=require('node:child_process'),assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const pw=require(process.env.PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const root=process.env.AURORA_TEST_ROOT||path.resolve(__dirname,'..');
 const out=process.env.AURORA_TEST_ARTIFACT_DIR||path.join(root,'validation',require('./package.json').version,'screenshots');fs.mkdirSync(out,{recursive:true});
 const server=spawn(process.env.AURORA_TEST_PYTHON||'python',['-m','uvicorn','tests.browser_release_server:app','--host','127.0.0.1','--port','8128'],{cwd:root,stdio:['ignore','ignore','pipe']});let browser;
 try{
  await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('startup timeout')),15000);server.stderr.on('data',d=>{if(d.toString().includes('Application startup complete')){clearTimeout(timer);resolve()}})});
  browser=await pw.chromium.launch({executablePath:process.env.CHROMIUM_PATH,args:['--no-sandbox','--disable-gpu','--no-zygote']});
  const page=await browser.newPage({viewport:{width:1366,height:900}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/news',r=>r.fulfill({json:{status:'unavailable',items:[],updated_at:null}}));
  await page.request.post('http://127.0.0.1:8128/_test/public_sources');
  let first=true,attempts=0;
  await page.route('**/api/instruments?**',r=>{attempts++;if(first){first=false;return r.fulfill({status:503,json:{detail:'Catálogo indisponível: teste controlado.'}})}return r.continue()});
  await page.goto('http://127.0.0.1:8128');
  await page.getByRole('alert').filter({hasText:'Catálogo indisponível: teste controlado.'}).waitFor();
  assert.equal(await page.getByText('Carregando catálogo...', {exact:true}).count(),0,'A failed request must not leave an endless loading label');
  await page.getByRole('button',{name:'Tentar novamente',exact:true}).click({timeout:3000});
  await page.locator('.asset-grid > button[title*="Disponível para visualizar"]').first().waitFor();assert(attempts>=2);
  assert.equal(await page.getByRole('alert').filter({hasText:'Catálogo indisponível: teste controlado.'}).count(),0);
  await page.getByLabel('Pesquisar ativo').fill('BTC/USDT');await page.getByText('BTC/USDT',{exact:true}).first().waitFor();
  await page.getByLabel('Pesquisar ativo').fill('BTC/USD');await page.getByText('BTC/USD',{exact:true}).first().waitFor();
  await page.getByLabel('Pesquisar ativo').fill('');
  for(const width of [1366,390,360]){await page.setViewportSize({width,height:950});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(out,'audit-1264-'+width+'.png'),fullPage:true})}
  assert.deepEqual(errors,[]);console.log('PASS catalogue HTTP 503 without endless loading, retry recovery, provider-separated instruments, desktop/mobile and no JavaScript exceptions');
 }finally{if(browser)await browser.close();server.kill()}
})().catch(e=>{console.error(e);process.exitCode=1});
