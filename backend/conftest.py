"""Ordinary offline entry; live PostgreSQL requires run_pg_tests.py."""
from tests.postgres import plugin

def pytest_addoption(parser,pluginmanager):
    if not pluginmanager.is_registered(plugin):
        pluginmanager.register(plugin,"paperpilot_pg_safety")
