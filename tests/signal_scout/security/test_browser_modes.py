"""Real headless production and native WSLg debugging behavior."""

from contextlib import contextmanager
import copy
import json
import selectors
import subprocess
import time
import uuid

import pytest
import yaml

from tests.signal_scout.support.stack import lifecycle
from tests.signal_scout.support.browser import fixture_web, SETUP
from tests.signal_scout.support.browser_policy import OBSERVE, settings
from tests.signal_scout.support import browser as boundary
from tests.signal_scout.support import browser_policy as safeguards
from tests.signal_scout.support import browser as isolation
from tests.signal_scout.support.native_window import NativeWindow


@contextmanager
def configured_mode(stack, directory, mode):
    directory.mkdir()
    config = settings.load_settings(lifecycle.CONFIG)
    config['browser']['mode'] = mode
    path = directory / 'config.yaml'
    path.write_text(yaml.safe_dump(config))
    override = directory / 'images.yaml'
    override.write_text(yaml.safe_dump({'configs': {'scout-operating': {'file': str(path)}}}))
    yield lifecycle.BrowserRun(compose_files=(*stack.browser_compose_files, override), config_path=path)


def probe(run, code, timeout=90):
    options = [value for key,setting in run.scout_environment().items() for value in ('-e',key+'='+setting)]
    return lifecycle.checked(run.compose + ['run','--rm','--no-deps','-T',*options,
        '--entrypoint','python','scout','-c',code], timeout=timeout)


@pytest.fixture(scope='module')
def headed_web(scout_stack, tmp_path_factory, native_display):
    directory = tmp_path_factory.mktemp('headed-web')/'images'
    with configured_mode(scout_stack,directory,'headed') as configured:
        previous = (scout_stack.compose_command,scout_stack.browser_compose_files,scout_stack.browser_config_path)
        scout_stack.compose_command = configured.compose
        scout_stack.browser_compose_files = (lifecycle.COMPOSE,directory/'images.yaml')
        scout_stack.browser_config_path = directory/'config.yaml'
        generator = fixture_web.__wrapped__(scout_stack,tmp_path_factory)
        try:
            yield next(generator)
        finally:
            try:
                next(generator,None)
            finally:
                scout_stack.compose_command,scout_stack.browser_compose_files,scout_stack.browser_config_path = previous


@pytest.mark.parametrize('value', [None, 'auto', True, []])
def test_SR_011_invalid_or_missing_mode_fails_explicitly(tmp_path, value):
    config = settings.load_settings(lifecycle.CONFIG)
    if value is None:
        del config['browser']['mode']
    else:
        config['browser']['mode'] = value
    path = tmp_path / 'invalid.yaml'
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match=r'^Invalid Scout operating settings\.$'):
        settings.load_settings(path)


def test_SR_012_topology_rejects_published_ports_and_extra_networks():
    run = lifecycle.BrowserRun()
    lifecycle.validate_topology(run.model, run.config)
    for service in ('scout', 'browser', 'proxy', 'firewall'):
        model = copy.deepcopy(run.model)
        model['services'][service]['ports'] = [{'host_ip':'127.0.0.1', 'target':9222, 'published':'9222'}]
        with pytest.raises(ValueError, match='Scout network configuration mismatch'):
            lifecycle.validate_topology(model, run.config)
    for change in ('external', 'extra-network', 'extra-grant'):
        model = copy.deepcopy(run.model)
        if change == 'external':
            model['networks']['restricted']['internal'] = False
        elif change == 'extra-network':
            model['networks']['desktop'] = {}
        else:
            model['services']['firewall']['networks']['outbound'] = {}
        with pytest.raises(ValueError, match='Scout network configuration mismatch'):
            lifecycle.validate_topology(model, run.config)


def test_SR_011_SR_012_production_headless_has_no_host_display(scout_stack, monkeypatch):
    monkeypatch.delenv('WAYLAND_DISPLAY', raising=False)
    monkeypatch.delenv('XDG_RUNTIME_DIR', raising=False)
    monkeypatch.setattr(lifecycle.wayland, 'resolve_wayland', lambda: pytest.fail('Headless discovered a display'))
    with scout_stack.browser_run() as run:
        assert run.config['browser']['mode'] == 'headless' and run._run_config.wayland is None
        info = run.inspect_owned()
        assert [m['Destination'] for m in info['Mounts'] if m['Type']=='bind'] == ['/opt/scout/config.yaml']
        assert not info['HostConfig']['PortBindings']
        assert set(run.inspect_helper('firewall')['NetworkSettings']['Networks']) == {run.model['networks']['restricted']['name']}
        probe(run, OBSERVE + r'''
with Browser() as browser, connect(os.environ['BROWSER_CDP_URL'],proxy=None,open_timeout=2) as observer:
    assert dimensions(attach(pages()[0]))==browser.settings['browser']['viewport']
''')
        lifecycle.checked(['docker','exec',run.container,'python3','-c',r'''
from pathlib import Path
import json
ready=json.loads(Path('/tmp/browser-ready.json').read_text())
assert b'--headless=new' in Path(f"/proc/{ready['pid']}/cmdline").read_bytes().split(b'\0')
environment=Path(f"/proc/{ready['pid']}/environ").read_bytes().split(b'\0')
assert not any(s.startswith((b'DISPLAY=',b'WAYLAND_DISPLAY=',b'XDG_RUNTIME_DIR=',b'PULSE_SERVER=')) for s in environment)
assert not Path('/run/scout-wayland/wayland-0').exists()
assert not any((p/'comm').read_text().strip() in {'Xvfb','x11vnc'} for p in Path('/proc').iterdir() if p.name.isdigit())
'''])


def test_SR_011_SR_012_native_headed_uses_only_granted_socket(scout_stack, tmp_path, native_display):
    with configured_mode(scout_stack,tmp_path/'native','headed') as run, run:
        info = run.inspect_owned()
        binds={m['Destination']:m for m in info['Mounts'] if m['Type']=='bind'}
        assert set(binds)=={'/opt/scout/config.yaml',str(lifecycle.wayland.SOCKET)}
        assert binds[str(lifecycle.wayland.SOCKET)]['Source']==str(run._run_config.wayland.source)
        assert binds[str(lifecycle.wayland.SOCKET)]['RW'] is False
        for service in ('proxy','firewall'):
            assert [m['Destination'] for m in run.inspect_helper(service)['Mounts'] if m['Type']=='bind']==['/opt/scout/config.yaml']
        lifecycle.checked(['docker','exec',run.container,'python3','-c',r'''
from pathlib import Path
import json
ready=json.loads(Path('/tmp/browser-ready.json').read_text())
assert b'--ozone-platform=wayland' in Path(f"/proc/{ready['pid']}/cmdline").read_bytes().split(b'\0')
environment=Path(f"/proc/{ready['pid']}/environ").read_bytes().split(b'\0')
assert b'WAYLAND_DISPLAY=/run/scout-wayland/wayland-0' in environment
assert not any(s.startswith((b'DISPLAY=',b'PULSE_SERVER=')) for s in environment)
runtime=next(s.split(b'=',1)[1].decode() for s in environment if s.startswith(b'XDG_RUNTIME_DIR='))
assert Path(runtime).stat().st_mode & 0o777==0o700
assert Path(runtime).stat().st_uid==10000
assert Path('/run/scout-wayland/wayland-0').is_socket()
assert not any((p/'comm').read_text().strip() in {'Xvfb','x11vnc'} for p in Path('/proc').iterdir() if p.name.isdigit())
'''])
        probe(run,OBSERVE + r'''
with Browser() as browser, connect(os.environ['BROWSER_CDP_URL'],proxy=None,open_timeout=2) as observer:
    assert dimensions(attach(pages()[0]))==browser.settings['browser']['viewport']
''')


def test_SR_012_SR_013_native_window_mouse_keys_and_stale_scout_reference_rejects(headed_web):
    stack, _, _, _ = headed_web
    with stack.browser_run() as run:
        options = [value for key,setting in run.scout_environment().items() for value in ('-e',key+'='+setting)]
        code = SETUP + r'''
with Browser() as browser:
    adapter=browser.session();tab=adapter._tabs()[0]['target_id']
    gate=browser.session(target_id=tab)
    successful(gate,'navigate',{'url':'http://public.test/gate'});wait_ready(browser)
    snapshot=successful(gate,'snapshot')['snapshot']
    title="Scout native input "+__import__("uuid").uuid4().hex
    browser.command("eval", "document.title="+json.dumps(title))
    points=browser.command('eval',"""(() => {
      const point=id=>{const r=document.querySelector('#'+id).getBoundingClientRect();
        return [(outerWidth-innerWidth)/2+r.x+r.width/2,
                outerHeight-innerHeight+r.y+r.height/2];};
      return {click:point('click'),query:point('query'),width:outerWidth,height:outerHeight};
    })()""")['result']
    print(json.dumps({'title':title, **points}),flush=True);input()
    actual=browser.command('eval',"""(async () => {
      const deadline=Date.now()+10000;
      while(window.clicks!==1 || document.querySelector('#query').value!=='initialhuman') {
        if(Date.now()>deadline)break;await new Promise(resolve=>setTimeout(resolve,50));
      }
      return {clicks:window.clicks,query:document.querySelector('#query').value,
              focus:document.activeElement.id,innerWidth,innerHeight,outerWidth,outerHeight,screenX,screenY};
    })()""")['result']
    assert actual['clicks']==1 and actual['query']=='initialhuman',actual
    print('manual-ok',flush=True)
    print('manual-wait',flush=True);input()
    deadline=time.monotonic()+10
    while browser.command('eval','window.clicks')['result']!=2:
        assert time.monotonic()<deadline,'Manual click did not arrive'
        time.sleep(.05)
    browser.command('eval',"document.querySelector('#click').replaceWith(document.querySelector('#click').cloneNode(true));true")
    result=dispatch(gate,'click',{'ref':reference(snapshot,'Controlled click')})
    assert result['status']=='rejected' and result['execution_started'] is False
    assert browser.command('eval','window.submits')['result']==0
    assert browser.command('eval','window.clicks')['result']==2
    print('stale-ok',flush=True);input()
    snapshot=successful(gate,'snapshot')['snapshot']
    browser.command('eval',"document.querySelector('#click').onclick=()=>window.clicks++;true")
    successful(gate,'click',{'ref':reference(snapshot,'Controlled click')})
    assert browser.command('eval','window.clicks')['result']==3
    print('done',flush=True)
'''
        child = subprocess.Popen(run.compose+['run','--rm','--no-deps','-T',*options,
            '--entrypoint','python','scout','-u','-c',code], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
        def line():
            with selectors.DefaultSelector() as ready:
                ready.register(child.stdout,selectors.EVENT_READ)
                assert ready.select(45), 'Controlled Scout/native-window synchronization timed out'
            value=child.stdout.readline().decode().strip()
            if not value:
                _, errors = child.communicate(timeout=30)
                pytest.fail('Controlled Scout fixture exited: ' + errors.decode()[-2000:])
            return value
        def resume():
            child.stdin.write(b'\n');child.stdin.flush()
        try:
            points=json.loads(line())
            window = NativeWindow(points['title'])
            observed = window.inspect()
            assert observed['visible'] is True and observed['width'] > 0 and observed['height'] > 0
            window.click(*points['click'], points['width'], points['height'])
            window.click(*points['query'], points['width'], points['height'])
            window.type('human')
            resume()
            try:
                assert line()=='manual-ok';assert line()=='manual-wait'
            except BaseException as error:
                raise AssertionError({'page':points,'native':observed}) from error
            window.click(*points['click'], points['width'], points['height'])
            resume();assert line()=='stale-ok'
            assert run.inspect_owned()['State']['Running']
            resume();assert line()=='done'
            _, errors=child.communicate(timeout=30)
            assert child.returncode==0, errors[-2000:]
        finally:
            if child.poll() is None:
                child.terminate();child.communicate(timeout=30)


def test_SR_013_SR_005_closing_native_window_ends_owned_browser(scout_stack, tmp_path, native_display):
    with configured_mode(scout_stack, tmp_path/'native-close', 'headed') as run, run:
        identity = run.container
        title = 'Scout native close ' + uuid.uuid4().hex
        probe(run, SETUP + '\nwith Browser() as browser:\n    browser.command("eval", ' +
              repr('document.title=' + json.dumps(title)) + ')\n')
        window = NativeWindow(title)
        assert window.inspect()['visible'] is True
        window.close()
        deadline = time.monotonic() + 15
        while run.inspect_owned()['State']['Running']:
            assert time.monotonic() < deadline, 'Native window closure did not end Chromium'
            time.sleep(.05)
        with pytest.raises(RuntimeError, match='Browser control unavailable'):
            run.scout_environment()
        assert lifecycle.checked(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.service=browser',
            '--filter', 'label=com.docker.compose.project=signal-scout-bootstrap']).strip() == identity[:12]
    assert subprocess.run(['docker', 'inspect', identity], capture_output=True).returncode != 0


def test_SR_011_SR_001_SR_002_SR_019_headed_sandbox_public_web_and_network_denials(headed_web):
    boundary.assert_SR_001_native_chromium_sandbox_and_anonymous_identity(headed_web)
    boundary.assert_SR_019_public_http_https_tls_and_forbidden_main_redirects(headed_web)
    boundary.assert_SR_002_background_http_websocket_cannot_control_cdp(headed_web)
    isolation.assert_SR_004_SR_019_browser_direct_egress_and_unauthorized_control_denied(headed_web[0])


def test_SR_011_SR_006_SR_007_SR_008_SR_009_SR_010_headed_context_viewport_and_native_download_denial(headed_web):
    stack = headed_web[0]
    safeguards.assert_SR_008_SR_009_main_window_tabs_share_contents_and_popup_stays_separate(stack)
    for kind in ('attachment','redirect','blob'):
        safeguards.assert_SR_006_SR_010_native_download_attempts_cancel_after_startup_disconnect_and_media_loads(headed_web,kind)
