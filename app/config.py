import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

class ConfigurationError(ValueError):
    """Safe, application-authored message; must never include input values."""

@dataclass
class Config:
    admin_token: str = ''  # Legacy constructor compatibility; ignored, no authentication.
    provider: str = 'deriv'
    strategy: str = 'scan_all'  # Manual Price Action for binary-option signals.
    confirm_htf: bool = False
    asset_scope: str = 'legacy'
    assets: list[str] = field(default_factory=lambda: ['EUR/USD', 'GBP/USD', 'USD/JPY', 'AUD/USD', 'USD/CAD', 'USD/CHF', 'NZD/USD'])
    telegram_token: str = ''
    telegram_chat: str = ''
    groq_api_key: str = field(default='',repr=False)
    groq_model: str = 'llama-3.3-70b-versatile'
    nvidia_api_key: str = field(default='',repr=False)
    nvidia_trend_model: str = 'meta/llama-3.3-70b-instruct'
    nvidia_nemotron_model: str = 'nvidia/llama-3.1-nemotron-70b-instruct'
    nvidia_glm_model: str = 'z-ai/glm-5-3'
    nvidia_kimi_model: str = ''
    gemini_api_key: str = field(default='',repr=False)
    gemini_model: str = field(default='',repr=False)
    database: str = 'data/aurora.db'
    min_lead: int = 60
    max_age: int = 330
    experimental_strategies: tuple[str, ...] = ()  # Shadow diagnostics only; never emit.
    experimental_collect: bool = False
    experimental_filters: tuple[str, ...] = ()
    selection_observation: bool = False
    nemotron_api_key: str = field(default='',repr=False)
    glm_api_key: str = field(default='',repr=False)
    kimi_api_key: str = field(default='',repr=False)

    def validate(self):
        from .strategies import RETIRED_KEYS
        if self.strategy in RETIRED_KEYS:
            self.strategy = 'scan_all'
        if isinstance(self.experimental_strategies, tuple):
            previous_experiments = self.experimental_strategies
            self.experimental_strategies = tuple(k for k in self.experimental_strategies if k not in RETIRED_KEYS)
            if previous_experiments and not self.experimental_strategies:
                self.experimental_collect = False
        if type(self.selection_observation) is not bool:
            raise ConfigurationError('SELECTION_OBSERVATION aceita somente true ou false.')
        if type(self.experimental_collect) is not bool or (self.experimental_collect and not self.experimental_strategies):
            raise ConfigurationError('EXPERIMENTAL_COLLECT exige true/false e módulos experimentais selecionados.')
        from .experiments import KEYS
        from .research_filters import FILTERS
        if (not isinstance(self.experimental_filters,tuple) or any(k not in FILTERS for k in self.experimental_filters)
                or len(set(self.experimental_filters))!=len(self.experimental_filters)):
            raise ConfigurationError('EXPERIMENTAL_FILTERS: use nomes únicos dos filtros de pesquisa ou deixe vazio.')
        if (not isinstance(self.experimental_strategies, tuple)
                or any(key not in KEYS for key in self.experimental_strategies)
                or len(set(self.experimental_strategies)) != len(self.experimental_strategies)):
            raise ConfigurationError('EXPERIMENTAL_STRATEGIES: use nomes únicos dos módulos experimentais ou deixe vazio.')
        # Upgrade older .env files without changing the source of historical signals.
        if self.provider == 'twelvedata': self.provider = 'deriv'
        if self.provider not in ('deriv','binance','coinbase'):
            raise ConfigurationError('Fonte inválida: use deriv, binance ou coinbase.')
        if self.asset_scope in ('forex','crypto_binary') and self.provider!='deriv':
            raise ConfigurationError('Fonte operacional incompatível: somente deriv em opções binárias.')
        from .strategies import STRATEGIES
        if self.strategy not in STRATEGIES:
            raise ConfigurationError('Estratégia inválida: ' + ', '.join(STRATEGIES))
        if self.confirm_htf is not False:
            raise ConfigurationError('CONFIRM_HTF deve permanecer false: ativação pendente de medição real do tempo por ativo.')
        if not 60 <= self.min_lead <= 180 or not 300 <= self.max_age <= 360:
            raise ConfigurationError('MIN_LEAD_SECONDS: 60–180; MAX_DATA_AGE_SECONDS: 300–360.')
        if self.asset_scope not in ('legacy','forex','crypto_binary'):
            raise ConfigurationError('Escopo de ativos inválido.')
        maximum_assets=18 if self.asset_scope=='crypto_binary' else 8
        if not 1 <= len(self.assets) <= maximum_assets or len(set(self.assets)) != len(self.assets):
            raise ConfigurationError(f'Configure entre 1 e {maximum_assets} ativos únicos.')
        if self.asset_scope=='forex':
            from .source_routing import FOREX_ASSETS
            if any(asset not in FOREX_ASSETS for asset in self.assets):
                raise ConfigurationError('ASSETS deve conter somente pares da allowlist Forex.')
        if self.asset_scope=='crypto_binary':
            from .source_routing import CRYPTO_BINARY_ASSETS
            if any(asset not in CRYPTO_BINARY_ASSETS for asset in self.assets):
                raise ConfigurationError('ASSETS deve conter somente os 18 pares cripto /USD de opções binárias.')
        # Syntax is source-specific; actual support remains the provider allowlist
        # and check_asset(), never inferred solely from a valid-looking symbol.
        pattern=(r'[A-Z0-9]{2,10}/USD' if self.asset_scope=='crypto_binary' else
                 r'[A-Z]{3}/[A-Z]{3}' if self.asset_scope=='forex' or self.provider=='deriv' else
                 r'[A-Z0-9]{2,10}/[A-Z0-9]{2,10}')
        if any(not isinstance(x,str) or not re.fullmatch(pattern,x) or 'OTC' in x for x in self.assets):
            raise ConfigurationError('Ativos inválidos para o mercado operacional configurado.')
        if bool(self.telegram_token) != bool(self.telegram_chat):
            raise ConfigurationError('Configure TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID juntos.')
        if self.telegram_token and not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+', self.telegram_token):
            raise ConfigurationError('Formato de TELEGRAM_BOT_TOKEN inválido.')
        if self.telegram_chat and not re.fullmatch(r'-?[0-9]+|@[A-Za-z0-9_]+', self.telegram_chat):
            raise ConfigurationError('Formato de TELEGRAM_CHAT_ID inválido.')
        return self

    @classmethod
    def load(cls):
        load_dotenv(ROOT / '.env', encoding='utf-8-sig')
        htf = os.getenv('CONFIRM_HTF', 'false').lower()
        if htf not in ('true', 'false'):
            raise ConfigurationError('CONFIRM_HTF aceita somente true ou false.')
        observe=os.getenv('SELECTION_OBSERVATION','false').lower()
        if observe not in ('true','false'):raise ConfigurationError('SELECTION_OBSERVATION aceita somente true ou false.')
        collect=os.getenv('EXPERIMENTAL_COLLECT','false').lower()
        if collect not in ('true','false'):raise ConfigurationError('EXPERIMENTAL_COLLECT aceita somente true ou false.')
        def integer(name, default):
            try: return int(os.getenv(name,default))
            except ValueError:
                raise ConfigurationError(name + ' deve ser um numero inteiro. Remova textos e comentarios do valor.') from None
        from .source_routing import CRYPTO_BINARY_ASSETS, FOREX_ASSETS
        assets=[asset.strip() for asset in os.getenv('ASSETS',','.join(CRYPTO_BINARY_ASSETS)).split(',')]
        # Existing installations may already use the seven currency pairs.
        # Infer their scope from the exact allowlist; never rename symbols or
        # replace an existing ASSETS value during an upgrade.
        scope=os.getenv('ASSET_SCOPE','').strip().lower()
        if scope and scope not in ('forex','crypto_binary'):
            raise ConfigurationError('ASSET_SCOPE aceita somente forex ou crypto_binary.')
        if not scope:
            scope='forex' if assets and all(asset in FOREX_ASSETS for asset in assets) else 'crypto_binary'
        return cls(provider=os.getenv('DATA_PROVIDER','deriv'),
            strategy=os.getenv('STRATEGY','scan_all'),
            confirm_htf=htf == 'true',
            asset_scope=scope,
            assets=assets,
            telegram_token=os.getenv('TELEGRAM_BOT_TOKEN',''), telegram_chat=os.getenv('TELEGRAM_CHAT_ID',''),
            groq_api_key=os.getenv('GROQ_API_KEY',''),groq_model=os.getenv('GROQ_MODEL','llama-3.3-70b-versatile'),
            nvidia_api_key=os.getenv('NVIDIA_API_KEY',''),
            nemotron_api_key=os.getenv('NEMOTRON_API_KEY',''),
            glm_api_key=os.getenv('GLM_API_KEY',''),
            kimi_api_key=os.getenv('KIMI_API_KEY',''),
            nvidia_trend_model=os.getenv('NVIDIA_TREND_MODEL','meta/llama-3.3-70b-instruct'),
            nvidia_nemotron_model=os.getenv('NVIDIA_NEMOTRON_MODEL','nvidia/llama-3.1-nemotron-70b-instruct'),
            nvidia_glm_model=os.getenv('NVIDIA_GLM_MODEL','z-ai/glm-5-3'),
            nvidia_kimi_model=os.getenv('NVIDIA_KIMI_MODEL',''),
            gemini_api_key=os.getenv('GEMINI_API_KEY',''), gemini_model=os.getenv('GEMINI_MODEL',''),
            database=os.getenv('DATABASE_PATH',str(ROOT / 'data/aurora.db')),
            experimental_strategies=tuple(key.strip() for key in os.getenv('EXPERIMENTAL_STRATEGIES','').split(',') if key.strip()),
            experimental_collect=collect=='true',
            selection_observation=observe=='true',
            experimental_filters=tuple(key.strip() for key in os.getenv('EXPERIMENTAL_FILTERS','').split(',') if key.strip()),
            min_lead=integer('MIN_LEAD_SECONDS','60'), max_age=integer('MAX_DATA_AGE_SECONDS','330')).validate()
