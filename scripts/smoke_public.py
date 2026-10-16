"""Read-only smoke test for the authenticated AURORA dashboard deployment.

Never submits credentials, starts the radar, places trades, or modifies settings.
Usage:
    python scripts/smoke_public.py --url https://your-domain.up.railway.app
For CI *inside* a container, HTTP loopback is explicitly allowed and Host can
be overridden to model an HTTPS reverse proxy terminating TLS at Railway.
"""
from __future__ import annotations

import argparse
import ipaddress
from urllib.parse import urlsplit

import httpx


class InvalidTarget(ValueError):
    pass


def validate_url(value: str, *, allow_local_http: bool = False) -> str:
    if not isinstance(value, str) or value.strip() != value:
        raise InvalidTarget('Informe uma URL sem espaços adicionais.')
    parts = urlsplit(value)
    if (not parts.hostname or parts.username is not None or parts.password is not None
            or parts.path not in ('', '/') or parts.query or parts.fragment):
        raise InvalidTarget('Informe somente a origem do domínio, sem senha, rota ou parâmetros.')
    try:
        if parts.port is not None and not 1 <= parts.port <= 65535:
            raise ValueError()
    except ValueError:
        raise InvalidTarget('Porta inválida.') from None
    if parts.scheme == 'https':
        return value.rstrip('/')
    if parts.scheme == 'http' and allow_local_http:
        try:
            loopback = ipaddress.ip_address(parts.hostname).is_loopback
        except ValueError:
            loopback = parts.hostname == 'localhost'
        if loopback:
            return value.rstrip('/')
    raise InvalidTarget('É obrigatório usar HTTPS, exceto HTTP loopback em teste local explícito.')


def inspect(base_url: str, *, client: httpx.Client) -> list[str]:
    """Check public login and anonymous access restrictions using GET only."""
    problems: list[str] = []
    expected = {
        '/healthz': 200,
        '/login': 200,
        '/api/state': 401,
        '/api/history': 401,
        '/api/history/days': 401,
        '/api/history/daily-summary': 401,
        '/api/credentials': 401,
        '/api/diagnostics': 401,
        '/api/diagnostics/logs': 401,
        '/api/validation': 401,
        '/api/validation/planetary-performance': 401,
        '/api/news': 401,
        '/api/instruments': 401,
        '/': 200,
        '/registro': 200,
        '/bot': 303,
        '/sinais': 303,
        '/historico': 303,
        '/calendario': 303,
        '/noticias': 303,
        '/configuracoes': 303,
        '/aprendizado': 303,
        '/telegram': 303,
        '/universo-ia': 303,
        '/validacao': 303,
        '/static/login.css': 200,
        '/static/portal.css': 200,
    }
    for route, status in expected.items():
        try:
            response = client.get(base_url + route)
        except httpx.RequestError:
            problems.append(f'{route}: servidor inacessível ou conexão interrompida.')
            continue
        if response.status_code != status:
            problems.append(f'{route}: HTTP {response.status_code}; esperado HTTP {status}.')
            continue
        if status == 303 and response.headers.get('location') != '/login':
            problems.append(f'{route}: redirecionamento não aponta para /login.')
        if route == '/healthz':
            try:
                if response.json() != {'status': 'ok'}:
                    problems.append('/healthz: conteúdo inesperado.')
            except ValueError:
                problems.append('/healthz: resposta JSON inválida.')
        if route == '/login' and ('type="password"' not in response.text or 'AURORA' not in response.text):
            problems.append('/login: formulário de acesso não encontrado.')
        if route.startswith('/api/'):
            if 'no-store' not in response.headers.get('cache-control', '').lower():
                problems.append('/api/state: resposta anônima sem cache-control no-store.')
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--allow-local-http', action='store_true',
                        help='Somente para testes HTTP loopback dentro do CI')
    parser.add_argument('--host', help='Host HTTPS encaminhado pelo proxy; somente em loopback')
    args = parser.parse_args(argv)
    try:
        base = validate_url(args.url, allow_local_http=args.allow_local_http)
        if args.host and not (args.allow_local_http and urlsplit(base).scheme == 'http'):
            raise InvalidTarget('Override de Host permitido somente em teste local.')
    except InvalidTarget as exc:
        parser.error(str(exc))
    headers = {'Host': args.host} if args.host else {}
    with httpx.Client(timeout=5, follow_redirects=False, trust_env=False, headers=headers) as client:
        errors = inspect(base, client=client)
    for error in errors:
        print('FALHA:', error)
    if errors:
        print(f'Deploy NÃO aprovado: {len(errors)} falha(s).')
        return 1
    print('Verificação pública aprovada: healthcheck, login, CSS e bloqueios anônimos.')
    print('Não comprova build React, autorização real, contratos da corretora ou qualidade dos sinais.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
