"""Short credentials must not corrupt versions, identifiers or timestamps."""
import pytest
from app import logging_setup


@pytest.mark.parametrize('secret', ['1', '123', '-123'])
def test_short_registered_secret_is_redacted_as_a_value_not_inside_metadata(monkeypatch, secret):
    monkeypatch.setattr(logging_setup, 'SECRETS', {secret})
    metadata = '1.24.9 / 1.22.10 / 1800000123 / xabcx / 2026-10-04T23:01:23+00:00'
    assert logging_setup.sanitize(metadata) == metadata
    assert logging_setup.sanitize({'token':secret, 'chat_id':secret}) == {'token':'[OCULTO]', 'chat_id':'[OCULTO]'}
    assert secret not in logging_setup.sanitize('credential='+secret+'; next')
    assert logging_setup.sanitize(secret) == '[OCULTO]'


@pytest.mark.parametrize('secret',['abc','TEST_ONLY_PRIVATE_TOKEN_abcdef123456'])
def test_nonnumeric_secret_still_redacted_when_embedded_and_urls_never_exposed(monkeypatch,secret):
    monkeypatch.setattr(logging_setup,'SECRETS',{secret})
    assert secret not in logging_setup.sanitize('prefix'+secret+'suffix')
    assert logging_setup.sanitize('https://example.invalid/?credential='+secret) == '[URL OCULTA]'
