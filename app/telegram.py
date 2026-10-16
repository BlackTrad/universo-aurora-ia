from .logging_setup import event, traced
import io
import asyncio
import time
import hashlib
import math
from datetime import datetime
from zoneinfo import ZoneInfo
from PIL import Image,ImageDraw,ImageFont
from .config import ROOT
from . import explanations
from .delivery_policy import SEND_TIMEOUT_SECONDS, minimum_lead, can_start, classify_ack

TZ = ZoneInfo('America/Sao_Paulo')
text_units = explanations.text_units
# Public compatibility helpers are also imported by regression tests.
compact_complete_text = explanations.compact_complete_text

class TelegramDeferred(Exception):
    def __init__(self,delay):self.delay=delay;super().__init__('Telegram cooldown')

def retry_delay(data,default=5):
    parameters=data.get('parameters',{}) if isinstance(data,dict) else {}
    value=parameters.get('retry_after') if isinstance(parameters,dict) else None
    return value if type(value) is int and value>0 else default

def rate_rejection(response,data):
    return (isinstance(data,dict) and data.get('ok') is not True and response.status_code<500
            and (response.status_code==429 or (data.get('ok') is False and data.get('error_code')==429)))

def telegram_failure(response,data):
    if rate_rejection(response,data):
        return {'status':'repetir','code':'rate_limit','retry_after':retry_delay(data)}
    code=data.get('error_code',response.status_code) if isinstance(data,dict) else response.status_code
    if response.status_code>=500:
        return {'status':'incerto','code':'server_error','detail':'Telegram sem confirmação; sem reenvio automático para evitar duplicidade.'}
    details={400:('destination','Destino ou mensagem recusados pelo Telegram. Confira o chat e as permissões.'),
             401:('authentication','Token Telegram recusado. Confira e salve a conexão.'),
             403:('permission','Bot sem permissão para publicar neste destino.'),
             404:('authentication','Token ou recurso Telegram não encontrado.')}
    if code in details:
        name,detail=details[code];return {'status':'falha','code':name,'detail':detail}
    return {'status':'incerto','code':'invalid_response','detail':'Resposta sem confirmação do envio; confira o chat. Sem reenvio automático.'}
def confirmed_message_id(response,data):
    """A truthy value or contradictory HTTP status is not delivery evidence."""
    if response.status_code!=200 or not isinstance(data,dict) or data.get('ok') is not True:
        return None
    result=data.get('result')
    mid=result.get('message_id') if isinstance(result,dict) else None
    return mid if type(mid) is int and mid>0 else None

def local(t,pattern='%d/%m/%Y %H:%M:%S'):
    return datetime.fromtimestamp(t,TZ).strftime(pattern)


def ai_summary(s):
    consensus=s.get('ai_consensus') if isinstance(s,dict) else None
    if not isinstance(consensus,dict) or not consensus.get('approved'):
        return ''
    decision=consensus.get('decision') if isinstance(consensus.get('decision'),dict) else {}
    direction=decision.get('direcao')
    if direction not in ('CALL','PUT'):
        return ''
    votes=consensus.get('votes') if isinstance(consensus.get('votes'),list) else []
    aligned=sum(1 for vote in votes if isinstance(vote,dict) and vote.get('status')=='disponivel'
                and not vote.get('dados_insuficientes') and vote.get('direcao')==direction)
    confidence=decision.get('confianca')
    confidence_label=f" · evidência {float(confidence):.0f}%" if isinstance(confidence,(int,float)) else ''
    return f"🤖Consenso IA: {direction} · {aligned}/{len(votes)}{confidence_label}"

def truncate(value,budget):
    if text_units(value)<=budget: return value
    output=''
    for char in value:
        if text_units(output+char+'…')>budget: break
        output+=char
    return output+'…'

def caption(s):
    minutes=s['duration_seconds']//60
    ai=ai_summary(s)
    message=(f"💱Ativo: {s['asset']}\n"
        f"🕐Entrada: {local(s['entry_at'],'%H:%M')}\n"
        f"{'⬆️' if s['direction']=='CALL' else '⬇️'}Direção: {'Compra' if s['direction']=='CALL' else 'Venda'}\n"
        f"⌛Expiração: {minutes} {'Minuto' if minutes==1 else 'Minutos'}\n")
    if ai:
        message += f"{ai}\n"
    message += "\n"
    message += "⛔SEM MARTINGALE⛔"
    if text_units(message)>1024:
        raise ValueError('Metadados excedem o limite da legenda.')
    return message


def result_caption(s,totals):
    cancelled=s.get('state')=='cancelado' and s.get('result')=='CANCELADO'
    icon={'WIN':'✅','LOSS':'❌','EMPATE':'➖','CANCELADO':'🚫'}.get(s['result'],'⚪')
    minutes=s['duration_seconds']//60
    ai=ai_summary(s)
    text=(f"{icon} {'ENTRADA CANCELADA' if cancelled else 'RESULTADO: '+s['result']} {icon}\n\n"
          f"💱Ativo: {s['asset']}\n🕐Entrada: {local(s['entry_at'],'%H:%M')}\n"
          f"{'⬆️' if s['direction']=='CALL' else '⬇️'}Direção: {'Compra' if s['direction']=='CALL' else 'Venda'}\n"
          f"⌛Expiração: {minutes} {'Minuto' if minutes==1 else 'Minutos'}\n\n")
    if ai:
        text += f"{ai}\n\n"
    if cancelled:text+=f"⚠️Não entrar: {truncate(s.get('cancellation_reason','Setup não confirmado.'),180)}\n\n"
    if s.get('expiry_at') is not None:text+=f"🕔Fim: {local(s['expiry_at'],'%d/%m %H:%M:%S')} · Brasília\n"
    rate=totals['win_rate_pct']
    label=f'{rate:.2f}%'.replace('.',',') if rate is not None else '—'
    return text+f"🏆Vitórias: {totals['wins']} · Derrotas: {totals['losses']}\n🎯Taxa de acerto: {label}"


def card(s):
    im=Image.new('RGB',(1080,860),'#090f1b');d=ImageDraw.Draw(im)
    # Use OS/Pillow fonts without distributing font binaries in source archives.
    from pathlib import Path
    choices=(ROOT/'assets/DejaVuSans.ttf',
             Path('C:/Windows/Fonts/arial.ttf'),
             Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))
    fontpath=next((str(path) for path in choices if path.is_file()), None)
    def load_font(size):
        if fontpath:
            try:return ImageFont.truetype(fontpath,size)
            except OSError:pass
        try:return ImageFont.truetype('DejaVuSans.ttf',size)
        except OSError:return ImageFont.load_default(size=size)
    def text(x,y,value,size=58,fill='#f1f5fa'):
        font=load_font(size)
        while d.textlength(value,font=font)>928 and size>14:
            size-=1;font=load_font(size)
        d.text((x,y),value,font=font,fill=fill)
    color='#42e3af' if s['direction']=='CALL' else '#ff6b80'
    d.rounded_rectangle((32,32,1048,828),radius=36,fill='#111d2d',outline='#29394a',width=2)
    text(76,118,'Ativo: '+s['asset'])
    text(76,255,'Entrada: '+local(s['entry_at'],'%H:%M'))
    text(76,392,'Direção: '+('↑ Compra' if s['direction']=='CALL' else '↓ Venda'),fill=color)
    minutes=s['duration_seconds']//60
    text(76,529,f"Expiração: {minutes} {'Minuto' if minutes==1 else 'Minutos'}")
    text(76,707,'SEM MARTINGALE',size=48,fill='#ff6b80')
    output=io.BytesIO();im.save(output,format='PNG');return output.getvalue()


class Telegram:
    def __init__(self,config,client):
        self.config,self.client=config,client
        self._send_lock=asyncio.Lock();self._last_post=None;self._telegram_retry_until=0
    @property
    def configured(self): return bool(self.config.telegram_token and self.config.telegram_chat)

    def destination_key(self):
        # Compare destination without persisting a token or chat credential.
        bot=self.config.telegram_token.partition(':')[0]
        return hashlib.sha256((bot+'\0'+str(self.config.telegram_chat)).encode()).hexdigest()

    async def _post(self,method,*,entry_at=None,required_lead=60,**kwargs):
        async with self._send_lock:
            if self._telegram_retry_until>time.monotonic():
                raise TelegramDeferred(max(1,math.ceil(self._telegram_retry_until-time.monotonic())))
            if self._last_post is not None:
                delay=1.05-(time.monotonic()-self._last_post)
                if delay>0:await asyncio.sleep(delay)
            if entry_at is not None and time.time()+SEND_TIMEOUT_SECONDS+required_lead>entry_at:return None
            self._last_post=time.monotonic()
            response=await self.client.post(f'https://api.telegram.org/bot{self.config.telegram_token}/{method}',**kwargs)
            event('telegram.response',http_status=response.status_code)
            try:data=response.json()
            except ValueError:data=None
            if rate_rejection(response,data):self._telegram_retry_until=time.monotonic()+retry_delay(data)
            return response

    @traced('telegram.result')
    async def send_result(self,s,totals):
        if not self.configured: return {'status':'não configurado'}
        cancelled=s.get('state')=='cancelado' and s.get('result')=='CANCELADO'
        if not cancelled and (s.get('settlement_pending') or s.get('state','encerrado')!='encerrado'
                or s.get('result') not in ('WIN','LOSS','EMPATE','Resultado indisponível')):
            return {'status':'falha','code':'result_pending','detail':'Apuração ainda não concluída; nenhuma mensagem de resultado enviada.'}
        original=s.get('telegram',{})
        if original.get('destination_key') and original['destination_key']!=self.destination_key():
            return {'status':'falha','code':'destination_changed','detail':'Destino Telegram alterado após o sinal. Resultado preservado no histórico; não enviado a outro destino.'}
        text=result_caption(s,totals)
        try:
            payload={'chat_id':self.config.telegram_chat,'text':text}
            mid=original.get('message_id')
            if original.get('status')=='enviado' and type(mid) is int and mid>0:
                payload['reply_parameters']={'message_id':mid,'allow_sending_without_reply':True}
            response=await self._post('sendMessage',json=payload)
            data=response.json()
            mid=confirmed_message_id(response,data)
            if mid is not None:
                return {'status':'enviado','message_id':mid}
            return telegram_failure(response,data)
        except TelegramDeferred as exc:
            return {'status':'repetir','code':'rate_limit','retry_after':exc.delay}
        except Exception:
            return {'status':'incerto','detail':'Envio não confirmado; sem reenvio automático.'}

    @traced('telegram.check')
    async def check(self):
        if not self.configured: return {'status':'não configurado','detail':'Salve o token e o destino do Telegram em Conexões antes de testar.'}
        try:
            response=await self._post('sendMessage',
                json={'chat_id':self.config.telegram_chat,'text':
                    '✅ AURORA — TESTE DE CONEXÃO\n\n'
                    'Mensagem de teste solicitada pelo painel.\n'
                    'O bot conseguiu enviar uma mensagem para este destino.\n'
                    f'🕒 {local(time.time())} · Horário de Brasília\n\n'
                    'Este é apenas um teste. Não é um sinal de operação.'})
            if response.status_code>=500:
                return {'status':'incerto','detail':'O Telegram não confirmou o teste. Confira o chat antes de tentar novamente; não haverá reenvio automático.'}
            data=response.json()
            mid=confirmed_message_id(response,data)
            if mid is not None:
                return {'status':'conectado','detail':'Mensagem de teste enviada e confirmada pelo Telegram. Confira o chat configurado.','message_id':mid}
            code=data.get('error_code',response.status_code)
            if code in (401,404): detail='Token do bot inválido. Corrija e salve o token em Conexões.'
            elif code==403: detail='O bot não tem permissão para enviar. Confira se foi bloqueado ou se precisa ser administrador do canal.'
            elif code==400: detail='Destino recusado. Confira o ID do chat e inicie uma conversa com o bot ou adicione-o ao grupo/canal.'
            elif code==429: detail='Limite de mensagens do Telegram atingido. Aguarde antes de testar novamente.'
            else: return {'status':'incerto','detail':'Resposta sem confirmação do envio. Confira o chat; não haverá reenvio automático.'}
            return {'status':'falha','detail':detail}
        except TelegramDeferred:
            return {'status':'falha','detail':'Telegram em pausa por limite de envio. Aguarde antes de testar novamente.'}
        except Exception:
            event('telegram.exception',exc_info=True)
            return {'status':'incerto','detail':'Envio do teste sem confirmação. Confira o chat antes de repetir; não haverá reenvio automático.'}

    @traced('telegram.send')
    async def send(self,s):
        if not self.configured: return {'status':'não configurado','detail':'Sinal preservado no painel.'}
        started_at=time.time()
        if not can_start(s,started_at):
            return {'status':'expirado','detail':'Envio bloqueado: prazo de entrada insuficiente.'}
        try:
            legend=caption(s)
            image=card(s)
        except Exception:
            event('telegram.exception',exc_info=True)
            return {'status':'falha','detail':'Falha ao preparar a imagem ou o texto; nada foi enviado. Sinal preservado no painel.'}
        if not can_start(s,time.time()):
            return {'status':'expirado','detail':'Envio bloqueado após preparar imagem/explicação: prazo de entrada insuficiente.'}
        # Only explicit 429 rejection is retriable. Timeouts/5xx can have accepted the photo.
        destination=self.destination_key()
        try:
            response=await self._post('sendPhoto',entry_at=s['entry_at'],required_lead=minimum_lead(s),
                data={'chat_id':str(self.config.telegram_chat),'caption':legend},
                files={'photo':('aurora-sinal.png',image,'image/png')})
            if response is None:
                return {'status':'expirado','detail':'Prazo de entrada insuficiente após aguardar a fila Telegram.'}
            data=response.json()
            mid=confirmed_message_id(response,data)
            if mid is not None:
                return classify_ack(s,{'status':'enviado','message_id':mid,'destination_key':destination},time.time(),started_at)
            return {**telegram_failure(response,data),'destination_key':destination}
        except TelegramDeferred as exc:
            return {'status':'repetir','code':'rate_limit','retry_after':exc.delay,'destination_key':destination}
        except Exception:
            event('telegram.exception',exc_info=True)
            return {'status':'incerto','detail':'Envio sem confirmação; sem reenvio automático para evitar duplicidade.','destination_key':destination}
