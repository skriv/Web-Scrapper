"""Unit tests never contact the live Maps service implicitly."""
import pytest
from unittest.mock import AsyncMock

@pytest.fixture(autouse=True)
def offline_maps_search(monkeypatch):
    from app import maps
    monkeypatch.setattr(maps,'search_maps',AsyncMock(return_value=[]))
