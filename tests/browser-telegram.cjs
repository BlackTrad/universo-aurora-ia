// Synthetic market and response metadata only; no real external requests.
const {spawn}=require('node:child_process'),assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const pw=require(process.env.PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const root=path.resolve(__dirname,'..'),out=process.env.AURORA_TEST_ARTIFACT_DIR||path.join(root,'validation',require('./package.json').version,'screenshots');fs.mkdirSync(out,{recursive:true});
 const server=spawn(process.env.AURORA_TEST_PYTHON||'python',['-m','uvicorn','tests.browser_release_server:app','--host','127.0.0.1','--port','8119'],{cwd:root,stdio:['ignore','ignore','pipe']});let browser;
 try{
  await new Promise((resolve,reject)=>{const t=setTimeout(()=>reject(Error('startup timeout')),15000);server.stderr.on('data',d=>{if(d.toString().includes('Application startup complete')){clearTimeout(t);resolve()}})});
  browser=await pw.chromium.launch({executablePath:process.env.CHROMIUM_PATH,args:['--no-sandbox','--disable-gpu','--no-zygote']});
  const page=await browser.newPage({viewport:{width:1366,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text())});page.on('response',r=>{if(r.url().includes('/api/')&&r.status()>=400)errors.push('HTTP '+r.status()+' '+new URL(r.url()).pathname)});
  await page.request.post('http://127.0.0.1:8119/_test/public_sources');
  const signal=(await(await page.request.post('http://127.0.0.1:8119/_test/emit_catalogue_signal?source=coinbase')).json()).signal;
  await page.request.post('http://127.0.0.1:8119/_test/resolve_catalogue_signal?sid='+signal.id);
  let delivery='pendente';
  await page.route('**/api/history?**',async r=>{const data=await(await r.fetch()).json();data.items=data.items.map(s=>s.id===signal.id?{...s,telegram_result:{status:delivery,detail:'Diagnóstico simulado de entrega.'}}:s);await r.fulfill({json:data})});
  for(const [status,label] of [['pendente','aguardando envio'],['repetir','reenvio agendado'],['incerto','sem confirmação'],['enviado','enviado']]){
   delivery=status;await page.goto('http://127.0.0.1:8119/historico');
   await page.getByLabel('Entrega do resultado no Telegram',{exact:true}).filter({hasText:'Telegram: '+label}).waitFor();
   assert(await page.getByRole('cell').filter({hasText:/^WIN/}).count());
  }
  for(const width of [1366,390]){await page.setViewportSize({width,height:900});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(out,'telegram-history-'+width+'.png'),fullPage:true})}
  await page.goto('http://127.0.0.1:8119/conexoes');
  await page.getByText('Explicações dos sinais: motor técnico local. Sem chave ou consumo de IA.',{exact:true}).waitFor();
  assert.equal(await page.getByLabel('Chave Gemini',{exact:true}).count(),0);
  assert.equal(await page.getByRole('heading',{name:'Spectro Ai · Gemini',exact:true}).count(),0);
  await page.screenshot({path:path.join(out,'motor-connections-mobile.png'),fullPage:true});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(errors,[]);
  console.log('PASS Telegram result status independent of WIN, four delivery states, engine-only connections, desktop/mobile, no JS or API errors');
 }finally{if(browser)await browser.close();server.kill()}
})().catch(e=>{console.error(e);process.exitCode=1});
