
from tests.signal_scout.support.browser import docker, IMPORT

from pathlib import Path

import pytest

PUBLIC4 = '11.203.247.2'
PUBLIC6 = '2606:4700:ffff:fffe::2'
NETWORK = 'signal-scout-protection-fixture'
BASE = '/tmp/scout-network-fixture'


def test_SR_001_native_chromium_sandbox_and_anonymous_identity(fixture_web):
    assert_SR_001_native_chromium_sandbox_and_anonymous_identity(fixture_web)


def test_SR_001_unavailable_namespace_sandbox_prevents_start(scout_stack, tmp_path):
    compose = ['docker', 'compose', '-f', str(Path(__file__).resolve().parents[3] / 'docker/signal-scout/compose.yaml')]
    override = tmp_path / 'default-seccomp.yaml'
    override.write_text('services:\n  browser:\n    security_opt: !override [no-new-privileges:true]\n')
    run = scout_stack.browser_run(compose_files=(*scout_stack.browser_compose_files, override))
    with pytest.raises(RuntimeError, match='Browser startup failed'):
        with run:
            pytest.fail('Browser started without required namespace sandbox')
    assert run.container is None
    assert not docker('ps', '-aq', '--filter', 'label=com.docker.compose.service=browser',
                      '--filter', 'label=com.docker.compose.project=signal-scout-bootstrap')


def test_SR_019_public_http_https_tls_and_forbidden_main_redirects(fixture_web):
    assert_SR_019_public_http_https_tls_and_forbidden_main_redirects(fixture_web)


def test_SR_019_dns_rebinding_rechecks_new_connections(fixture_web):
    stack, _, counts, baseline = fixture_web
    stack.browser_probe(IMPORT + """
with Browser(config) as browser:
    # Response changes DNS answer while this same browser remains alive.
    browser.navigate('http://rebind.test/rebind')
    assert browser.command('eval', 'window.fixtureReady')['result'] is True
    try:
        browser.navigate('http://rebind.test/second')
    except RuntimeError:
        pass
    else:
        raise AssertionError('Rebound destination reached')
""")
    assert counts()['172.30.242.2:80'] == baseline['172.30.242.2:80']


def test_SR_002_background_http_websocket_cannot_control_cdp(fixture_web):
    'SR_019 also: background denial leaves allowed main document alive.'
    assert_SR_002_background_http_websocket_cannot_control_cdp(fixture_web)

from tests.signal_scout.support.browser import assert_SR_001_native_chromium_sandbox_and_anonymous_identity, assert_SR_019_public_http_https_tls_and_forbidden_main_redirects, assert_SR_002_background_http_websocket_cannot_control_cdp
