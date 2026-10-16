import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.config import Config
from tests.test_system import Feed,TG

@pytest.mark.parametrize('page',['analise','historico','conexoes','configuracoes','noticias'])
def test_page_url_and_fragment(tmp_path,page):
    with TestClient(create_app(Config(database=str(tmp_path/'db')),Feed(),TG())) as client:
        response=client.get('/'+page)
        assert response.status_code==200 and '/static/assets/' in response.text
        import re
        assets=re.findall(r'(?:src|href)="(/static/assets/[^"]+)"',response.text)
        assert assets
        assert all(client.get(asset).status_code==200 for asset in assets)
        assert client.get('/static/app.js').status_code==404
        assert client.get('/api/unknown').status_code==404
