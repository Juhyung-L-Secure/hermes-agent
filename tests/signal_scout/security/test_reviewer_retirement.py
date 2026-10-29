from copy import deepcopy
import importlib
from pathlib import Path
from types import SimpleNamespace

import yaml

settings = importlib.import_module('plugins.signal-scout.security.settings')
gate_module = importlib.import_module('plugins.signal-scout.security.gate')
ROOT = Path(__file__).resolve().parents[3]


def test_AG_009_startup_without_reviewer_settings_executes_and_keeps_default_deny(tmp_path, monkeypatch):
    config = yaml.safe_load((ROOT / 'docker/signal-scout/config.yaml').read_text())
    config.pop('scout_reviewer', None)
    config['browser']['action_permissions'] = {'navigate': 'allowed', 'click': 'blocked'}
    path = tmp_path / 'config.yaml'
    path.write_text(yaml.safe_dump(config))
    browser = gate_module.Browser.__new__(gate_module.Browser)
    browser.settings = settings.load_settings(path)
    browser.ready, browser.failed, browser.endpoint = True, False, 'ws://owned-offline'
    monkeypatch.setenv('BROWSER_CDP_URL', browser.endpoint)
    browser.evidence = lambda: SimpleNamespace(tabs=lambda: [{'target_id': 'owned-page'}], _cdp=connection)
    connection = object()
    browser.verify_control = lambda: None
    effects = []
    gate = gate_module.BrowserActionGate(browser, target_id='owned-page')
    def execute(action, args, invoke):
        invoke(lambda: effects.append((action, deepcopy(args))))
        return {'snapshot': '- heading "Observed current page"', 'target_id': 'owned-page'}
    monkeypatch.setattr(gate, '_execute', execute)
    assert gate.dispatch({'action': 'navigate', 'args': {'url': 'http://public.test/'}}) == {
        'status': 'success', 'reason': None, 'execution_started': True,
        'data': {'snapshot': '- heading "Observed current page"', 'target_id': 'owned-page'}}
    for action, args in [('click', {'ref': '@s1'}), ('snapshot', {})]:
        assert gate.dispatch({'action': action, 'args': args}) == {
            'status': 'rejected', 'reason': 'Browser action is blocked.', 'execution_started': False, 'data': {}}
    assert effects == [('navigate', {'url': 'http://public.test/'})]
    assert browser.ready and not browser.failed
