"""Persistent spectator accounts. Administrative access is never self-registered."""
from __future__ import annotations

import re
import sqlite3
from threading import RLock
from pathlib import Path
import time
import uuid

from .public_auth import make_password_hash, verify_password_hash


class RegistrationError(ValueError):
    """Messages are authored here; never include submitted credentials."""


def normalized_email(value: str) -> str:
    email=value.strip().casefold()
    if len(email)>254 or not re.fullmatch(r'[^\s@<>\x00-\x1f]+@[^\s@<>\x00-\x1f]+\.[^\s@<>\x00-\x1f]+',email):
        raise RegistrationError('Informe um e-mail válido.')
    return email


class Accounts:
    def __init__(self,path: str):
        self.lock=RLock()
        if path!=':memory:':Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.connection=sqlite3.connect(path,check_same_thread=False,timeout=5)
        self.connection.row_factory=sqlite3.Row
        if path!=':memory:':Path(path).chmod(0o600)
        self.connection.execute('PRAGMA busy_timeout=5000')
        self.connection.execute('PRAGMA journal_mode=WAL')
        self.connection.execute('''CREATE TABLE IF NOT EXISTS accounts (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at REAL NOT NULL,
            active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1))
        )''')
        self.connection.commit()
        # Keep a comparable hash cost when an account does not exist.
        self.dummy_hash=make_password_hash(uuid.uuid4().hex)

    @staticmethod
    def profile(row) -> dict:
        return {'id':row['id'],'name':row['name'],'email':row['email'],'role':'viewer'}

    def create(self,name: str,email: str,password: str,confirmation: str) -> dict:
        name=name.strip()
        if not 2<=len(name)<=80 or re.search(r'[\x00-\x1f<>]',name):
            raise RegistrationError('Informe seu nome, com 2 a 80 caracteres.')
        email=normalized_email(email)
        if not 12<=len(password)<=256:
            raise RegistrationError('Use uma senha com 12 a 256 caracteres.')
        if password!=confirmation:
            raise RegistrationError('As senhas não coincidem.')
        password_hash=make_password_hash(password)
        identity=uuid.uuid4().hex
        with self.lock:
            try:
                self.connection.execute('INSERT INTO accounts (id,name,email,password_hash,created_at) VALUES (?,?,?,?,?)',
                                        (identity,name,email,password_hash,time.time()))
                self.connection.commit()
            except sqlite3.IntegrityError:
                self.connection.rollback()
                raise RegistrationError('Não foi possível criar a conta. Confira os dados ou entre se já tiver cadastro.') from None
        return {'id':identity,'name':name,'email':email,'role':'viewer'}

    def authenticate(self,identifier: str,password: str) -> dict | None:
        try:email=normalized_email(identifier)
        except RegistrationError:email=''
        with self.lock:
            row=self.connection.execute('SELECT * FROM accounts WHERE email=? AND active=1',(email,)).fetchone()
        valid=verify_password_hash(password,row['password_hash'] if row else self.dummy_hash)
        return self.profile(row) if valid and row else None

    def get(self,identity: str) -> dict | None:
        with self.lock:
            row=self.connection.execute('SELECT id,name,email FROM accounts WHERE id=? AND active=1',(identity,)).fetchone()
        return self.profile(row) if row else None

    def close(self):
        with self.lock:self.connection.close()
