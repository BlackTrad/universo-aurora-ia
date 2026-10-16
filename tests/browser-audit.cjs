// Synthetic server and controlled response races; no live messages.
const {spawn}=require('node:child_process');
const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const pw=require(process.env.PLAYWRIGHT_MODULE||(process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES?process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES+'/playwright':'playwright'));
(async()=>{
 const root=process.env.AURORA_TEST_ROOT||path.resolve(__dirname,'..');
 const server=spawn(process.env.AURORA_TEST_PYTHON||'python',['-m','uvicorn','tests.browser_release_server:app','--host','127.0.0.1','--port','8114'],{cwd:root,stdio:['ignore','ignore','pipe']});
 let browser;
 try{
  await new Promise((resolve,reject)=>{const t=setTimeout(()=>reject(Error('startup')),15000);server.stderr.on('data',d=>{if(d.toString().includes('Application startup complete')){clearTimeout(t);resolve()}})});
  browser=await pw.chromium.launch({executablePath:process.env.CHROMIUM_PATH,args:['--no-sandbox','--disable-gpu','--no-zygote']});
  const page=await browser.newPage({viewport:{width:1366,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  let arm=false,release,held;
  await page.route('**/api/state',async route=>{
   if(!arm)return route.continue();arm=false;
   const old=await(await route.fetch()).json();held();
   await new Promise(resolve=>release=resolve);await route.fulfill({json:old});
  });
  await page.goto('http://127.0.0.1:8114');
  await page.getByRole('button',{name:'Analisar agora',exact:true}).waitFor();
  await page.waitForFunction(async()=>!(await(await fetch('/api/state')).json()).busy);
  await page.getByText('Configurações',{exact:true}).click();
  const captured=new Promise(resolve=>held=resolve);arm=true;await captured;
  await page.getByLabel('Duração',{exact:true}).selectOption('120');
  await page.waitForFunction(()=>[...document.querySelectorAll('select')].some(s=>s.value==='M5')&&[...document.querySelectorAll('select')].some(s=>s.value==='120'));
  // A successful mutation refresh must overtake the deliberately held poll.
  await page.waitForTimeout(1000);release();await page.waitForTimeout(700);
  assert.equal(await page.getByLabel('Duração',{exact:true}).inputValue(),'120','stale poll overwrote the saved duration');
  console.log('PASS stale state cannot overwrite a newer refresh');
  await page.request.post('http://127.0.0.1:8114/_test/public_sources');
  const second=await browser.newPage();await second.goto('http://127.0.0.1:8114/conexoes');
  assert((await second.request.put('http://127.0.0.1:8114/api/connection',{data:{provider:'binance'}})).ok());
  await second.route('**/api/connection/check',async route=>{
   await new Promise(resolve=>setTimeout(resolve,16000));
   return route.fulfill({json:{status:'disponível',detail:'Consulta concluída no prazo do backend'}});
  });
  await second.getByRole('button',{name:'Verificar conexões',exact:true}).click();
  await second.getByText('Consulta concluída no prazo do backend',{exact:true}).waitFor();
  console.log('PASS 16s connection response survives the 17s backend budget');
  await page.waitForFunction(()=>document.body.textContent.includes('BTC/USDT'));
  assert(!await page.getByRole('button',{name:'EUR/USD',exact:true}).count());
  await page.getByRole('link',{name:'Painel',exact:true}).click();
  await page.getByText('Binance · Brasília (UTC−3)',{exact:false}).waitFor();
  await page.goto('http://127.0.0.1:8114/historico');
  await page.route('**/api/validation/groups?**',route=>route.fulfill({json:{items:[{strategy:'sr_volume_breakout',asset:'BTC/USDT',source_key:'binance',timeframe:'M5',duration_seconds:300,rules_version:'1.22.10',pipeline_version:'1.22.10',wins:23,losses:7,win_rate_pct:76.7}]}}));
  await page.reload();await page.getByText('Grupos com taxa observada',{exact:true}).first().click();await page.getByRole('heading',{name:'Grupos com taxa observada'}).waitFor();
  await page.setViewportSize({width:360,height:800});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'ranking overflows on mobile');
  const out=process.env.AURORA_TEST_ARTIFACT_DIR||path.join(root,'validation',require('./package.json').version,'screenshots');fs.mkdirSync(out,{recursive:true});
  await page.screenshot({path:path.join(out,'audit-history-mobile.png'),fullPage:true});
  assert.deepEqual(errors,[]);console.log('PASS cross-tab source sync, long history ranking at 360px, no JS exceptions');
 }finally{if(browser)await browser.close();server.kill()}
})().catch(e=>{console.error(e);process.exitCode=1});
