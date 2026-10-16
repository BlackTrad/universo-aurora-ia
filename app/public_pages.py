"""Public presentation and native authentication forms, independent of React."""
from html import escape

DISCLAIMER='Sinais gerados por análise técnica automatizada. Não constituem recomendação de investimento. Opções binárias envolvem alto risco de perda do capital.'
BRAND='<a class="brand" href="/" aria-label="Universo Aurora IA · Início"><span class="brand-mark" aria-hidden="true">◉</span><span>AURORA<small>UNIVERSO DE INTELIGÊNCIA</small></span></a>'


def document(title,body):
    return '''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<meta name="referrer" content="same-origin"/>
<meta name="theme-color" content="#070c14"/>
<meta name="description" content="Conheça o Universo Aurora IA: análise técnica, consenso entre agentes de IA, sinais de opções binárias e histórico em um painel espacial."/>
<title>'''+escape(title)+'''</title><link rel="stylesheet" href="/static/portal.css"/>
</head><body><a class="skip" href="#conteudo">Ir para o conteúdo</a>
<input class="motion-switch" type="checkbox" id="pausar-animacao"/>
<label class="motion-label" for="pausar-animacao"><span class="pause-text">Ⅱ Pausar animação</span><span class="play-text">▷ Retomar animação</span></label>
<div class="portal"><div class="starfield" aria-hidden="true"></div>'''+body+'''</div></body></html>'''


def landing_page(*,authenticated=False):
    entry='<a class="button primary small" href="/bot">Abrir painel <span aria-hidden="true">↗</span></a>' if authenticated else '<a class="nav-login" href="/login">Entrar</a><a class="button primary small" href="/registro">Criar conta <span aria-hidden="true">↗</span></a>'
    cta='/bot' if authenticated else '/registro'
    label='Explorar meu painel' if authenticated else 'Criar minha conta'
    return document('Universo Aurora IA · Análise que conecta perspectivas', '''
<header class="site-header"><div class="header-inner">'''+BRAND+'''
<nav aria-label="Navegação principal"><a href="#como-funciona">Como funciona</a><a href="#inteligencias">Inteligências</a><a href="#recursos">O painel</a></nav>
<div class="header-actions">'''+entry+'''</div></div></header>
<main id="conteudo">
<section class="hero wrap" aria-labelledby="hero-title">
 <div class="hero-copy reveal"><p class="eyebrow"><span class="tiny-orbit" aria-hidden="true"></span>UNIVERSO AURORA IA / OPÇÕES BINÁRIAS</p>
 <h1 id="hero-title">O mercado muda.<br/>Sua visão <em>se expande.</em></h1>
 <p class="lead">Análise técnica e diferentes inteligências conectadas para acompanhar o mercado em um só lugar. Explore os sinais, entenda cada entrada e consulte o que aconteceu depois.</p>
 <div class="hero-actions"><a class="button primary" href="'''+cta+'''">'''+label+''' <span aria-hidden="true">↗</span></a><a class="text-link" href="#como-funciona">Conhecer o projeto <span aria-hidden="true">↓</span></a></div>
 <p class="hero-note">Cadastro para acompanhar sinais. Decisão e execução sempre manuais.</p>
 <div class="hero-facts"><div><b>6</b><span>agentes de IA</span></div><div><b>CALL / PUT</b><span>direções de entrada</span></div><div><b>BRT</b><span>horário de Brasília</span></div></div>
 </div>
 <div class="universe-art reveal" role="img" aria-label="Ilustração de seis agentes de inteligência artificial em órbita do núcleo Aurora">
  <div class="art-grid"></div><div class="orbit orbit-one"></div><div class="orbit orbit-two"></div><div class="scan-ray"></div>
  <div class="aurora-core"><span class="core-sphere"></span><b>AURORA</b><small>CONSENSO CENTRAL</small></div>
  <div class="orb orb-groq"><i></i><b>Groq</b><span>Price Action</span></div>
  <div class="orb orb-nvidia"><i></i><b>NVIDIA</b><span>Tendência</span></div>
  <div class="orb orb-nemotron"><i></i><b>Nemotron</b><span>Estrutura</span></div>
  <div class="orb orb-glm"><i></i><b>GLM</b><span>Liquidez</span></div>
  <div class="orb orb-gemini"><i></i><b>Gemini</b><span>Confluências</span></div>
  <div class="orb orb-kimi"><i></i><b>Kimi</b><span>Análise crítica</span></div>
  <span class="art-caption">VISUALIZAÇÃO ILUSTRATIVA · AGENT TEAMS</span>
 </div>
</section>
<section class="workflow wrap section" id="como-funciona" aria-labelledby="workflow-title">
 <div class="section-heading"><p class="eyebrow">01 / DO DADO AO SINAL</p><h2 id="workflow-title">Uma entrada precisa<br/>de mais que um palpite.</h2><p>O bot organiza a análise em etapas. A IA participa depois que o motor técnico encontra um possível cenário de entrada.</p></div>
 <ol class="steps"><li><span class="step-number">01</span><div><h3>O mercado é observado</h3><p>O servidor consulta preços e candles dos pares monitorados e avalia as estratégias técnicas configuradas.</p></div></li><li><span class="step-number">02</span><div><h3>Os agentes confrontam o cenário</h3><p>As inteligências recebem o mesmo pacote de dados. Consenso e filtros de risco decidem se a oportunidade pode avançar.</p></div></li><li><span class="step-number">03</span><div><h3>Você acompanha a oportunidade</h3><p>Um sinal aprovado informa ativo, direção, horário de entrada, expiração e contexto. O histórico registra a apuração pela fonte de preços.</p></div></li></ol>
</section>
<section class="intelligence section" id="inteligencias" aria-labelledby="ai-title"><div class="wrap">
 <div class="section-heading wide"><p class="eyebrow">02 / PERSPECTIVAS INDEPENDENTES</p><h2 id="ai-title">Seis planetas.<br/><em>Um centro de decisão.</em></h2><p>Cada agente examina uma parte do cenário. Um voto isolado não autoriza uma entrada: o consenso também precisa passar pelas regras operacionais.</p></div>
 <div class="agent-list"><article><span class="agent-dot teal"></span><div><h3>Groq</h3><p>Comportamento do preço e leitura dos candles.</p></div><small>PRICE ACTION</small></article><article><span class="agent-dot gold"></span><div><h3>NVIDIA</h3><p>Direção predominante e contexto de tendência.</p></div><small>TENDÊNCIA</small></article><article><span class="agent-dot silver"></span><div><h3>Nemotron</h3><p>Estrutura do mercado e regiões técnicas.</p></div><small>ESTRUTURA</small></article><article><span class="agent-dot rose"></span><div><h3>GLM</h3><p>Zonas de liquidez e possíveis desequilíbrios.</p></div><small>LIQUIDEZ</small></article><article><span class="agent-dot teal"></span><div><h3>Gemini</h3><p>Convergência entre as evidências disponíveis.</p></div><small>CONFLUÊNCIAS</small></article><article><span class="agent-dot gold"></span><div><h3>Kimi</h3><p>Revisão crítica de riscos e inconsistências.</p></div><small>ANÁLISE CRÍTICA</small></article></div>
 <p class="fineprint">A disponibilidade depende dos provedores. Sem respostas válidas suficientes, o sistema não confirma o consenso. Confiança analítica não é probabilidade de lucro.</p>
</div></section>
<section class="section wrap instruments" id="recursos" aria-labelledby="tools-title">
 <div class="section-heading"><p class="eyebrow">03 / SEU CENTRO DE ACOMPANHAMENTO</p><h2 id="tools-title">Contexto antes.<br/>Transparência depois.</h2><p>Sua conta abre o painel compartilhado do projeto. Você acompanha o bot; o administrador gerencia o monitoramento e as configurações.</p><a class="text-link" href="'''+cta+'''">Entrar no universo <span aria-hidden="true">↗</span></a></div>
 <div class="tool-list"><article><span aria-hidden="true">⌁</span><div><h3>Gráfico de velas</h3><p>Selecione um ativo, confira a fonte e acompanhe o movimento do preço com horários em Brasília.</p></div></article><article><span aria-hidden="true">↗</span><div><h3>Sinais com contexto</h3><p>Direção CALL ou PUT, estratégia, contagem até a entrada e expiração. Sem setup válido, o bot aguarda.</p></div></article><article><span aria-hidden="true">◷</span><div><h3>Histórico pesquisável</h3><p>Consulte ativo, estratégia, preços e resultado indicativo. Filtre por dia e entenda o registro de cada sinal.</p></div></article><article><span aria-hidden="true">✧</span><div><h3>Votos das inteligências</h3><p>Veja quais agentes responderam, suas justificativas e o estado do consenso na página Universo IA.</p></div></article></div>
</section>
<section class="wrap section questions" aria-labelledby="faq-title"><div class="section-heading"><p class="eyebrow">ANTES DE ENTRAR EM ÓRBITA</p><h2 id="faq-title">O que você<br/>precisa saber.</h2></div><div class="faq-list">
 <details><summary>O bot opera o dinheiro da minha corretora?</summary><p>Não há execução automática de ordens. O projeto gera sinais para acompanhamento e eventual decisão manual na sua corretora.</p></details>
 <details><summary>O que recebo ao criar uma conta?</summary><p>Acesso aos sinais compartilhados, gráficos, histórico e estado das inteligências. As configurações, credenciais, controles de análise e Telegram são exclusivos do administrador.</p></details>
 <details><summary>O resultado do histórico é o mesmo da minha operação?</summary><p>O resultado do bot é indicativo, apurado pela fonte externa na entrada e expiração. Cotação, execução e payout da sua corretora podem ser diferentes.</p></details>
 <details><summary>O sistema usa Martingale ou garante lucro?</summary><p>Não usa Martingale ou Soros e não garante retorno. Uma sequência de sinais pode ter perdas, mesmo com consenso entre as inteligências.</p></details>
 <details><summary>Por que às vezes não aparece nenhum sinal?</summary><p>O bot aguarda se o monitoramento estiver pausado, o mercado ou a fonte estiverem indisponíveis, ou se os critérios técnicos, de consenso e de risco não forem atendidos.</p></details>
</div></section>
<section class="wrap final-invite"><p class="eyebrow">SEU PRÓXIMO PONTO DE OBSERVAÇÃO</p><h2>Entre no Universo Aurora.</h2><p>Um lugar para acompanhar os sinais e entender o contexto.</p><a class="button primary" href="'''+cta+'''">'''+label+''' <span aria-hidden="true">↗</span></a></section>
</main><footer class="site-footer wrap"><div>'''+BRAND+'''<a href="/login">Acessar minha conta ↗</a></div><p>'''+DISCLAIMER+'''</p><small>UNIVERSO AURORA IA · ANÁLISE TÉCNICA · DECISÃO MANUAL</small></footer>''')


def auth_page(*,register=False,error=''):
    heading='Crie seu acesso.' if register else 'Bem-vindo à sua órbita.'
    intro='Acompanhe os sinais, o mercado e as inteligências do Universo Aurora.' if register else 'Entre na sua conta para acompanhar o Universo Aurora IA.'
    fields='''<label for="name">Seu nome</label><input id="name" name="name" autocomplete="name" minlength="2" maxlength="80" required/>
<label for="email">E-mail</label><input id="email" name="email" type="email" autocomplete="email" maxlength="254" required/>''' if register else '''<label for="identifier">E-mail ou acesso de administrador</label><input id="identifier" name="identifier" autocomplete="username" maxlength="254" required placeholder="voce@exemplo.com ou admin"/>'''
    fields+='''<label for="password">Senha</label><input id="password" name="password" type="password" minlength="12" maxlength="256" autocomplete="'''+('new-password' if register else 'current-password')+'''" required aria-describedby="password-help"/>
<small id="password-help">'''+('Use pelo menos 12 caracteres. Sua senha é armazenada com hash.' if register else 'Administrador: use admin e sua senha atual.')+'''</small>'''
    if register:fields+='''<label for="confirmation">Confirme sua senha</label><input id="confirmation" name="confirmation" type="password" minlength="12" maxlength="256" autocomplete="new-password" required/>'''
    error_markup='<p class="form-error" role="alert">'+escape(error)+'</p>' if error else ''
    alternate='<p class="auth-alternate">Já tem uma conta? <a href="/login">Entrar</a></p>' if register else '<p class="auth-alternate">Primeira vez por aqui? <a href="/registro">Criar minha conta</a></p>'
    return document(('Criar conta' if register else 'Entrar')+' · Universo Aurora IA', '''
<header class="site-header"><div class="header-inner">'''+BRAND+'''<a class="text-link" href="/">← Conhecer o projeto</a></div></header>
<main id="conteudo" class="auth-layout wrap">
 <section class="auth-story"><p class="eyebrow">UNIVERSO AURORA IA</p><h1>Mais perspectivas.<br/><em>Uma só visão.</em></h1><p>Análise técnica, agentes de IA e histórico conectados em um painel de acompanhamento.</p><div class="auth-planet" aria-hidden="true"><span></span></div><ul><li>Sinais com entrada e expiração em Brasília</li><li>Gráficos e contexto de cada oportunidade</li><li>Resultados indicativos, sem execução automática</li></ul></section>
 <section class="auth-panel" aria-labelledby="auth-title"><span class="eyebrow">'''+('NOVO ACESSO' if register else 'ACESSO AO PAINEL')+'''</span><h2 id="auth-title">'''+heading+'''</h2><p>'''+intro+'''</p>'''+error_markup+'''
 <form method="post" action="/api/auth/'''+('register' if register else 'login')+'''">'''+fields+'''
 <button class="button primary" type="submit">'''+('Criar conta e entrar' if register else 'Entrar no painel')+''' <span aria-hidden="true">↗</span></button></form>'''+alternate+'''
 <p class="auth-footnote">'''+('O cadastro libera acompanhamento. Somente o administrador altera o bot. Nome e e-mail identificam sua conta neste projeto; não solicitamos acesso à sua corretora.' if register else 'Sua conexão usa HTTPS. A sessão expira em até 4 horas; depois, entre novamente.')+'''</p></section>
</main><footer class="auth-footer wrap"><p>'''+DISCLAIMER+'''</p></footer>''')
