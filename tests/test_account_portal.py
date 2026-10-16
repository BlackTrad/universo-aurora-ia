"""Account lifecycle and spectator isolation; no external APIs or notifications."""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from app.accounts import Accounts, RegistrationError
from app.config import Config
from app.main import create_app
from app.public_auth import PublicAuth, COOKIE_NAME, SESSION_SECONDS
from tests.test_public_auth import ORIGIN, PASSWORD, configured
from tests.test_system import Feed, TG
from tests.test_evolution_1250 import signal

USER_PASSWORD='spectator-only-test-password'
FORM={'name':'Observador de teste','email':'observer@example.invalid',
      'password':USER_PASSWORD,'confirmation':USER_PASSWORD}
HEADERS={'Origin':ORIGIN,'Sec-Fetch-Site':'same-origin'}


@pytest.fixture
def portal(tmp_path):
    with patch.dict(os.environ,configured(),clear=True):
        app=create_app(Config(database=str(tmp_path/'aurora.db')),Feed(),TG())
        with TestClient(app,base_url=ORIGIN,follow_redirects=False) as client:
            yield app,client


def register(client,**changes):
    return client.post('/api/auth/register',data={**FORM,**changes},headers=HEADERS)


def test_accounts_persist_normalize_and_never_store_plaintext(tmp_path):
    path=str(tmp_path/'accounts.db')
    accounts=Accounts(path)
    profile=accounts.create('Pessoa de teste','  PERSON@EXAMPLE.INVALID  ',USER_PASSWORD,USER_PASSWORD)
    assert profile['role']=='viewer' and profile['email']=='person@example.invalid'
    stored=accounts.connection.execute('SELECT password_hash FROM accounts').fetchone()[0]
    assert USER_PASSWORD not in stored and stored.startswith('pbkdf2_sha256$')
    accounts.close()
    reopened=Accounts(path)
    assert reopened.authenticate('Person@Example.Invalid',USER_PASSWORD)==profile
    assert reopened.authenticate('person@example.invalid','invalid') is None
    assert reopened.authenticate('missing@example.invalid',USER_PASSWORD) is None
    with pytest.raises(RegistrationError):reopened.create('Outro','person@example.invalid',USER_PASSWORD,USER_PASSWORD)
    assert os.stat(path).st_mode & 0o777==0o600
    reopened.close()


@pytest.mark.parametrize('changes',[
    {'name':'<script>'},{'email':'admin'},{'email':'a\n@b.invalid'},
    {'password':'short','confirmation':'short'},{'confirmation':'different'},
])
def test_invalid_registration_is_safe_and_does_not_create_accounts(portal,changes):
    app,client=portal
    response=register(client,**changes)
    assert response.status_code==422 and 'role="alert"' in response.text
    assert USER_PASSWORD not in response.text
    assert app.state.accounts.connection.execute('SELECT count(*) FROM accounts').fetchone()[0]==0


def test_duplicate_registration_is_atomic(tmp_path):
    accounts=Accounts(str(tmp_path/'users.db'))
    def create(_):
        try:return accounts.create('Teste','duplicate@example.invalid',USER_PASSWORD,USER_PASSWORD)
        except RegistrationError:return None
    with ThreadPoolExecutor(max_workers=3) as pool:
        results=list(pool.map(create,range(3)))
    assert sum(result is not None for result in results)==1
    accounts.close()


def test_public_presentation_and_native_form_origin_policy(portal):
    _,client=portal
    for route in ('/','/login','/registro'):
        response=client.get(route)
        assert response.status_code==200
        assert response.headers['referrer-policy']=='same-origin'
        assert '<meta name="referrer" content="same-origin"' in response.text
        assert 'no-referrer' not in response.text
        assert '/static/portal.css' in response.text
    assert 'O mercado muda.' in client.get('/').text
    assert 'action="/api/auth/register"' in client.get('/registro').text
    assert 'name="identifier"' in client.get('/login').text
    assert client.get('/static/portal.css').status_code==200
    assert client.get('/bot').headers['location']=='/login'
    assert client.get('/cadastro').headers['location']=='/registro'


@pytest.mark.parametrize('origin',[None,'null','https://foreign.example.org'])
@pytest.mark.parametrize('route',['/api/auth/register','/api/auth/login'])
def test_missing_null_and_foreign_origins_remain_blocked(portal,origin,route):
    _,client=portal
    headers={} if origin is None else {'Origin':origin}
    assert client.post(route,data=FORM,headers=headers).status_code==403


@pytest.mark.parametrize('site',['same-site','cross-site'])
def test_fetch_metadata_rejects_cross_origin_registration(portal,site):
    _,client=portal
    assert client.post('/api/auth/register',data=FORM,headers={**HEADERS,'Sec-Fetch-Site':site}).status_code==403


def test_signup_login_logout_and_existing_admin_access(portal):
    _,client=portal
    response=register(client)
    assert response.status_code==303 and response.headers['location']=='/bot'
    assert all(flag in response.headers['set-cookie'] for flag in ('Secure','HttpOnly','SameSite=strict'))
    assert client.get('/bot').status_code==200
    status=client.get('/api/auth/status').json()
    assert status['user']['role']=='viewer' and status['user']['name']==FORM['name']
    assert client.get('/api/state').json()['access']['user']==status['user']
    assert client.get('/login').headers['location']=='/bot'
    assert client.post('/api/auth/logout',headers={'Origin':'null'}).status_code==403
    assert client.post('/api/auth/logout',headers=HEADERS).status_code==303
    assert client.get('/api/state').status_code==401
    response=client.post('/api/auth/login',data={'identifier':FORM['email'].upper(),'password':USER_PASSWORD},headers=HEADERS)
    assert response.status_code==303 and response.headers['location']=='/bot'
    client.post('/api/auth/logout',headers=HEADERS)
    for credentials in ({'identifier':'admin','password':PASSWORD},{'password':PASSWORD}):
        assert client.post('/api/auth/login',data=credentials,headers=HEADERS).status_code==303
        assert client.get('/api/auth/status').json()['user']['role']=='admin'
        client.post('/api/auth/logout',headers=HEADERS)


def test_viewers_cannot_self_promote_or_access_any_admin_api(portal):
    app,client=portal
    assert register(client,role='admin').status_code==422
    assert register(client).status_code==303
    from app.access import VIEWER_READS
    tested=0
    for route in app.routes:
        path=getattr(route,'path','')
        if not path.startswith('/api/') or path.startswith('/api/auth/') or path in VIEWER_READS:continue
        path=path.replace('{sid}','synthetic-signal')
        for method in route.methods:
            response=client.request(method,path,headers=HEADERS)
            assert response.status_code==403,(method,path,response.status_code)
            tested+=1
    assert tested>=25
    assert client.get('/configuracoes').headers['location']=='/bot'
    assert client.get('/universo-ia').status_code==200
    assert client.get('/api/history/days').status_code==200
    assert not app.state.service.scan['active'] and app.state.service.telegram.calls==0


def test_snapshot_and_history_do_not_disclose_operator_data(portal):
    app,client=portal
    private='PRIVATE-OPERATOR-MARKER'
    original=signal(stake_brl=12345,estimated_pnl_brl=4567,
        telegram={'status':'enviado','destination_key':private,'message_id':888},
        telegram_result={'status':'enviado','message_id':999,'detail':private},
        ai_consensus={'approved':True,'private':private,'decision':{'direcao':'CALL','confianca':81,'private':private},
                      'votes':[{'agente':'Groq','direcao':'CALL','confianca':81,'status':'disponivel','private':private}]})
    app.state.service.store.add_signal(original,'test-public-isolation')
    assert register(client).status_code==303
    state=client.get('/api/state').json()
    history=client.get('/api/history').json()
    for response in (state,history):
        encoded=json.dumps(response)
        for field in (private,'stake_brl','estimated_pnl_brl','destination_key','message_id','credentials','configuration','manual_confirmations'):
            assert field not in encoded
    assert state['signals'][0]['asset']==original['asset']
    assert history['items'][0]['result']=='WIN'
    assert state['settings']['assets']==app.state.service.settings()['assets']


def test_viewer_cookie_cannot_be_upgraded_and_deactivated_account_is_rejected(portal):
    app,client=portal
    register(client)
    cookie=client.cookies.get(COOKIE_NAME)
    auth=PublicAuth.from_env()
    assert not auth.valid_cookie(cookie)
    identity=auth.user_id_from_cookie(cookie)
    assert identity
    assert auth.user_id_from_cookie(cookie+'x') is None
    parts=cookie.split('.')
    parts[2]='f'*32
    assert auth.user_id_from_cookie('.'.join(parts)) is None
    old=auth.new_user_cookie(identity,now=1_000_000_000)
    assert auth.user_id_from_cookie(old,now=1_000_000_000+SESSION_SECONDS) is None
    with app.state.accounts.lock,app.state.accounts.connection:
        app.state.accounts.connection.execute('UPDATE accounts SET active=0 WHERE id=?',(identity,))
    assert client.get('/api/state').status_code==401


def test_repeated_bad_logins_are_rate_limited_without_exposing_account_existence(portal):
    _,client=portal
    for _ in range(10):
        response=client.post('/api/auth/login',data={'identifier':'nobody@example.invalid','password':USER_PASSWORD},headers=HEADERS)
        assert response.status_code==303 and response.headers['location']=='/login?error=1'
    limited=client.post('/api/auth/login',data={'identifier':'nobody@example.invalid','password':USER_PASSWORD},headers=HEADERS)
    assert limited.status_code==429 and limited.headers['retry-after']=='300'
    assert client.post('/api/auth/login',data={'identifier':'admin','password':PASSWORD},headers=HEADERS).status_code==303
