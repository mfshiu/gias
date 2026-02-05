# tests/conftest.py
import pytest
from src.log_helper import init_logging

@pytest.fixture(scope="session", autouse=True)
def _init_test_logging():
    init_logging()
