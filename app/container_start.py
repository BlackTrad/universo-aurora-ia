"""Single-worker safe container launch. Public bind requires validated authentication."""
import os
import uvicorn
from .public_auth import PublicAuth
from .container_permissions import prepare_volume_and_drop


def settings():
    auth=PublicAuth.from_env()  # Fail closed before listening on a public interface.
    raw=os.getenv('PORT','8000')
    try:
        port=int(raw)
    except ValueError:
        raise SystemExit('PORT precisa ser um número inteiro.') from None
    if not (1<=port<=65535):
        raise SystemExit('PORT está fora do intervalo permitido.')
    return ('0.0.0.0' if auth.enabled else '127.0.0.1'),port


def main():
    host,port=settings()
    prepare_volume_and_drop()
    uvicorn.run('app.main:app',host=host,port=port,workers=1,
                proxy_headers=False,server_header=False)


if __name__=='__main__':
    main()
