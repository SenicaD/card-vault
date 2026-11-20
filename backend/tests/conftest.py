import pytest


@pytest.fixture(autouse=True)
def no_lookup_on_add(monkeypatch):
    """New cards stay unpriced unless a test turns the lookup on add back on."""
    from app.services import refresh
    monkeypatch.setattr(refresh, "LOOKUP_ON_ADD", False)
