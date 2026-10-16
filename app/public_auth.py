"""Opt-in authenticated public dashboard. No changes to loopback-only defaults."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from hashlib import pbkdf2_hmac, sha256
import hmac
import os
import re
import secrets
import time
from urllib.parse import urlsplit

from dotenv import load_dotenv
from .config import ROOT

ITERATIONS = 310_000
SESSION_SECONDS = 4 * 60 * 60
COOKIE_NAME = 'aurora_session'


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode('ascii').rstrip('=')


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


def make_password_hash(password: str, *, salt: bytes | None = None) -> str:
    if len(password) < 12:
        raise ValueError('Senha deve possuir pelo menos 12 caracteres.')
    salt = salt if salt is not None else secrets.token_bytes(16)
    if len(salt) != 16:
        raise ValueError('Salt inválido.')
    digest = pbkdf2_hmac('sha256', password.encode('utf-8'), salt, ITERATIONS)
    return f'pbkdf2_sha256${ITERATIONS}${_b64(salt)}${_b64(digest)}'


def verify_password_hash(password: str, encoded: str) -> bool:
    try:
        algorithm,count,salt,digest=encoded.split('$')
        rounds=int(count)
        if (algorithm!='pbkdf2_sha256' or not ITERATIONS<=rounds<=1_000_000 or
                len(_decode(salt))!=16 or len(_decode(digest))!=32 or len(password)>256):
            return False
        computed=pbkdf2_hmac('sha256',password.encode('utf-8'),_decode(salt),rounds)
        return hmac.compare_digest(computed,_decode(digest))
    except (ValueError,TypeError,UnicodeEncodeError,OverflowError):
        return False


@dataclass(frozen=True)
class PublicAuth:
    enabled: bool = False
    origin: str = ''
    password_hash: str = ''
    session_key: bytes = b''

    @classmethod
    def from_env(cls):
        load_dotenv(ROOT / '.env', encoding='utf-8-sig')
        enabled = os.getenv('AURORA_PUBLIC_MODE', 'false').strip().lower()
        if enabled not in ('true', 'false'):
            raise RuntimeError('AURORA_PUBLIC_MODE precisa ser true ou false.')
        if enabled == 'false':
            return cls()
        origin = os.getenv('AURORA_PUBLIC_ORIGIN', '').strip().rstrip('/')
        parsed = urlsplit(origin)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or
                parsed.password or parsed.path not in ('', '/') or parsed.query or
                parsed.fragment or parsed.port is not None or
                not re.fullmatch(r'[a-zA-Z0-9.-]+', parsed.hostname)):
            raise RuntimeError('Configure AURORA_PUBLIC_ORIGIN como uma origem HTTPS válida, sem caminho.')
        password_hash = os.getenv('AURORA_ADMIN_PASSWORD_HASH', '')
        try:
            algorithm, count, salt, digest = password_hash.split('$')
            if (algorithm != 'pbkdf2_sha256' or int(count) < ITERATIONS or
                    int(count) > 1_000_000 or len(_decode(salt)) != 16 or len(_decode(digest)) != 32):
                raise ValueError()
        except (ValueError, TypeError, OverflowError):
            raise RuntimeError('Configure AURORA_ADMIN_PASSWORD_HASH com o gerador incluído.') from None
        key = os.getenv('AURORA_SESSION_SECRET', '')
        if len(key) < 48 or len(set(key)) < 12:
            raise RuntimeError('AURORA_SESSION_SECRET precisa ser um segredo aleatório longo.')
        return cls(enabled=True, origin=origin, password_hash=password_hash, session_key=key.encode())

    @property
    def hostname(self) -> str:
        return urlsplit(self.origin).hostname or ''

    def correct_password(self, password: str) -> bool:
        return verify_password_hash(password,self.password_hash)

    def new_user_cookie(self,identity: str,now: int | None = None) -> str:
        if not re.fullmatch(r'[0-9a-f]{32}',identity):raise ValueError('Conta inválida.')
        expiry=int(time.time() if now is None else now)+SESSION_SECONDS
        payload=f'v2.{expiry}.{identity}.{secrets.token_hex(16)}'
        return payload+'.'+_b64(hmac.new(self.session_key,payload.encode(),sha256).digest())

    def user_id_from_cookie(self,value: str | None,now: int | None = None) -> str | None:
        if not value or len(value)>256:return None
        try:
            version,expiry,identity,nonce,signature=value.split('.')
            if version!='v2' or not re.fullmatch(r'[0-9]{10,12}',expiry):return None
            if not re.fullmatch(r'[0-9a-f]{32}',identity) or not re.fullmatch(r'[0-9a-f]{32}',nonce):return None
            current=int(time.time() if now is None else now)
            if not current<int(expiry)<=current+SESSION_SECONDS:return None
            payload=f'v2.{expiry}.{identity}.{nonce}'
            expected=_b64(hmac.new(self.session_key,payload.encode(),sha256).digest())
            return identity if hmac.compare_digest(expected,signature) else None
        except (ValueError,TypeError,OverflowError):return None

    def new_cookie(self, now: int | None = None) -> str:
        expiry = int(time.time() if now is None else now) + SESSION_SECONDS
        payload = f'v1.{expiry}.{secrets.token_hex(16)}'
        signature = _b64(hmac.new(self.session_key, payload.encode(), sha256).digest())
        return f'{payload}.{signature}'

    def valid_cookie(self, value: str | None, now: int | None = None) -> bool:
        if not value or len(value) > 256:
            return False
        try:
            version, expiry, nonce, signature = value.split('.')
            if version != 'v1' or not re.fullmatch(r'[0-9]{10,12}', expiry):
                return False
            if not re.fullmatch(r'[0-9a-f]{32}', nonce):
                return False
            current = int(time.time() if now is None else now)
            if int(expiry) <= current or int(expiry) > current + SESSION_SECONDS:
                return False
            expected = _b64(hmac.new(self.session_key, f'v1.{expiry}.{nonce}'.encode(), sha256).digest())
            return hmac.compare_digest(expected, signature)
        except (ValueError, TypeError):
            return False
