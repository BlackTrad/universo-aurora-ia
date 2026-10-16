"""Local synthetic UI harness; credentials saved only in a temporary directory."""
import tempfile
from pathlib import Path
from app.config import Config
from app.main import create_app
from tests.test_system import Feed, TG

temporary=tempfile.TemporaryDirectory(prefix='aurora-ui-test-')
app=create_app(Config(admin_token='local-validation-token-not-a-secret',database=':memory:'),
               Feed(),TG(),credentials_path=Path(temporary.name)/'.env')
