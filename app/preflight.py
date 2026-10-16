"""Print exact safe startup diagnostics, without displaying configuration values."""
import logging
from .config import Config, ConfigurationError, ROOT
from .logging_setup import configure, event

def main():
    configure()
    try:
        Config.load()
    except ConfigurationError as error:
        # Only typed, application-authored messages are safe to display.
        message=str(error)
        event('configuration.invalid',level=logging.ERROR)
        logging.getLogger('aurora').error(message)
        print('\nCONFIGURACAO: '+message, flush=True)
        print('Arquivo para revisar: '+str(ROOT/'.env'), flush=True)
        print('Salve a correcao e execute start.bat novamente. Nao e necessario apagar .venv.', flush=True)
        print('Para configurar Telegram pelo painel, deixe TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID ambos vazios no .env.', flush=True)
        print('Variaveis de ambiente do Windows, se definidas, tem prioridade sobre .env.', flush=True)
        return 2
    except ImportError:
        event('configuration.dependencies_missing',level=logging.ERROR,exc_info=True)
        print('Dependencia Python ausente. Execute .venv\\Scripts\\python.exe -m pip install -r requirements.txt')
        return 3
    except Exception:
        event('configuration.unexpected',level=logging.ERROR,exc_info=True)
        print('Falha ao ler a configuracao. Consulte logs/aurora.log. Nenhum valor de credencial foi registrado.')
        return 3
    event('configuration.valid')
    return 0

if __name__=='__main__':raise SystemExit(main())
