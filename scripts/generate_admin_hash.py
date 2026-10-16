"""Print a salted password hash and a separate random session secret. Never print the password."""
from getpass import getpass
from pathlib import Path
import secrets
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.public_auth import make_password_hash

password = getpass('Senha administrativa (12+ caracteres): ')
confirm = getpass('Confirme a senha: ')
if password != confirm:
    raise SystemExit('As senhas não coincidem.')
try:
    value = make_password_hash(password)
except ValueError as exc:
    raise SystemExit(str(exc)) from None
print('AURORA_ADMIN_PASSWORD_HASH=' + value)
print('AURORA_SESSION_SECRET=' + secrets.token_urlsafe(48))
print('Salve estes valores nas variáveis privadas da hospedagem, nunca no repositório.')
