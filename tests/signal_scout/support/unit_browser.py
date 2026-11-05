from copy import deepcopy
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

owner_module = importlib.import_module('plugins.signal-scout.scout_runtime.browser.owner')
from tools.interrupt import set_interrupt

cdp_module = importlib.import_module('plugins.signal-scout.scout_runtime.browser._cdp')
session_module = importlib.import_module('plugins.signal-scout.scout_runtime.browser.session')
settings = importlib.import_module('plugins.signal-scout.scout_runtime.settings')
CONFIG = Path(__file__).resolve().parents[3] / 'docker/signal-scout/config.yaml'


@pytest.fixture
def boundary(monkeypatch):
    browser = owner_module.Browser.__new__(owner_module.Browser)
    browser.settings = settings.load_settings(CONFIG)
    browser.ready, browser.failed, browser.endpoint = True, False, 'ws://trusted-owned-browser'
    monkeypatch.setenv('BROWSER_CDP_URL', browser.endpoint)
    calls, effects, closed = [], [], []
    session = session_module.BrowserSession(browser)
    session._cdp = SimpleNamespace(facts={}, effect_sent=0, effect_replies=0, capture_deadline=None)
    session._tabs = lambda: [{'target_id': 'owned-page'}]
    session._run = lambda operation: operation()
    session._snapshot = lambda tab, scan: session._inspect_snapshot(tab)
    browser._session = session
    browser.verify_control = lambda: None
    def close():
        browser.ready, browser.failed = False, True
        closed.append(True)
    browser.close = close
    session._bind('owned-page')
    def execute(action, args, invoke):
        calls.append(('execute', deepcopy(args)))
        invoke(lambda: effects.append((action, deepcopy(args))))
        return {'snapshot': '- heading "Current observation"', 'target_id': 'owned-page'}
    monkeypatch.setattr(session, '_execute', execute)
    yield SimpleNamespace(session=session, browser=browser,
                          calls=calls, effects=effects, closed=closed)
    set_interrupt(False)

def transport(messages=(), *, send_error=None, receive_error=None):
    pending, sent = iter(messages), []
    def send(message):
        sent.append(json.loads(message))
        if send_error:
            raise send_error
    def receive(**_):
        try:
            return json.dumps(next(pending))
        except StopIteration:
            raise receive_error or TimeoutError('fixture response deadline')
    cdp = cdp_module._CDP.__new__(cdp_module._CDP)
    cdp.timeout, cdp.sequence = 30, 0
    cdp.observation_timeout, cdp.interaction_observation_timeout = 5, 0.25
    cdp._observation = None
    cdp.authority, cdp.capture_deadline, cdp.facts = None, None, {}
    cdp.effect_sent = cdp.effect_replies = 0
    cdp.connection = SimpleNamespace(send=send, recv=receive)
    return cdp, sent


def navigation(cdp, url='http://public.test/', session='owned', frame='root', *, loader='current'):
    baseline = cdp_module._DocumentKey(session, frame, 'baseline', 1)
    current = cdp_module._DocumentKey(session, frame, loader, 2)
    return cdp.observe(lambda: cdp.command('Page.navigate', {'url':url}, session), baseline,
        lambda: current, lambda: cdp_module._Page(
            baseline if cdp._observation.native is cdp_module._NavigationOutcome.SAME_DOCUMENT else current,
            '- heading "Current"', url, 'interactive'),
        explicit=True).outcome
