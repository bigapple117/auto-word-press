import pytest


@pytest.fixture(autouse=True)
def _no_korea_relay(monkeypatch):
    """시험이 실제 한국 통로(127.0.0.1:18081)를 쓰지 않게 끈다. 통로 시험은 직접 켠다."""
    from src import source_fetcher
    monkeypatch.setattr(source_fetcher, "KOREA_PROXY", None)
    source_fetcher._dead_hosts.clear()
