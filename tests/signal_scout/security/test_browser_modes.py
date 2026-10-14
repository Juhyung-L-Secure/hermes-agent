"""SR_011–SR_013: real configured modes and trusted local viewer control."""

import base64
from contextlib import contextmanager
import copy
import http.client
import json
import selectors
import subprocess
import uuid

import pytest
import yaml
from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect

from tests.signal_scout.conftest import lifecycle
from tests.signal_scout.security.test_browser_boundary import fixture_web
from tests.signal_scout.security.test_browser_safeguards import OBSERVE, settings
from tests.signal_scout.security import test_browser_boundary as boundary
from tests.signal_scout.security import test_browser_safeguards as safeguards
from tests.signal_scout.security import test_isolation as isolation
from tests.signal_scout.security.test_gate_browser import SETUP
from tests.signal_scout.security.viewer_fixture import ViewerClient


@contextmanager
def configured_mode(stack, directory, mode, viewer):
    """Bake mode into existing four services, retaining fixture DNS and volumes."""
    directory.mkdir()
    config = settings.load_settings(lifecycle.CONFIG)
    config['browser']['mode'], config['browser']['viewer'] = mode, viewer
    path = directory / 'config.yaml'
    path.write_text(yaml.safe_dump(config))
    images, overrides = [], {}
    prefix = 'signal-scout-mode-fixture-' + uuid.uuid4().hex
    override = directory / 'images.yaml'
    try:
        for target, services in (('runtime',('scout',)), ('browser',('browser',)), ('network',('proxy','firewall'))):
            dockerfile = directory / ('Dockerfile.' + target)
            dockerfile.write_text('FROM signal-scout-bootstrap-' + target + ':local\nCOPY config.yaml /opt/scout/config.yaml\n')
            image = prefix + '-' + target + ':local'
            lifecycle.checked(['docker','build','-q','-t',image,'-f',str(dockerfile),str(directory)])
            images.append(image)
            for service in services:
                overrides[service] = {'image':image}
        override.write_text(yaml.safe_dump({'services':overrides}))
        yield lifecycle.BrowserRun(compose_files=(*stack.browser_compose_files, override), config_path=path)
    finally:
        lifecycle.BrowserRun().start_helpers()
        for image in images:
            lifecycle.checked(['docker','image','rm',image])


def probe(run, code, timeout=90):
    """Use configured runtime image and exact owned browser attachment."""
    options = [value for key,setting in run.scout_environment().items() for value in ('-e',key+'='+setting)]
    return lifecycle.checked(run.compose + ['run','--rm','--no-deps','-T',*options,
        '--entrypoint','python','scout','-c',code], timeout=timeout)


@pytest.fixture(scope='module')
def headed_web(scout_stack, tmp_path_factory):
    """Run existing controlled-web fixture against baked headed service images."""
    directory = tmp_path_factory.mktemp('headed-web')/'images'
    with configured_mode(scout_stack,directory,'headed',True) as configured:
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


def http_viewer(port, authorization=None, host=None, origin=None):
    """Check real HTTP authentication without including secret response data."""
    headers = {}
    if authorization is not None:
        headers['Authorization'] = authorization
    if host is not None:
        headers['Host'] = host
    if origin is not None:
        headers['Origin'] = origin
    client = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
    try:
        client.request('GET','/vnc.html',headers=headers)
        response = client.getresponse()
        return response.status, response.read()
    finally:
        client.close()


@pytest.mark.parametrize('case', ['mode-missing','mode-invalid','mode-bool','viewer-missing','viewer-text',
                                  'viewer-headless','viewer-port-missing','viewer-port-cdp','viewer-port-bool',
                                  'viewer-subnet-missing','viewer-address-missing','viewer-subnet-overlap',
                                  'viewer-subnet-public','viewer-address-outside','viewer-address-gateway',
                                  'viewer-address-ipv6'])
def test_SR_011_required_mode_and_viewer_settings_fail_explicitly(tmp_path, case):
    config = settings.load_settings(lifecycle.CONFIG)
    if case == 'mode-missing':
        del config['browser']['mode']
    elif case.startswith('mode-'):
        config['browser']['mode'] = True if case == 'mode-bool' else 'auto'
    elif case == 'viewer-missing':
        del config['browser']['viewer']
    elif case in {'viewer-text','viewer-headless'}:
        config['browser']['viewer'] = 'true' if case == 'viewer-text' else True
    elif case in {'viewer-port-cdp','viewer-port-bool'}:
        config['scout_network']['viewer_port'] = True if case == 'viewer-port-bool' else 9222
    elif case.endswith('-missing'):
        del config['scout_network'][case.removesuffix('-missing').replace('-','_')]
    else:
        key, value = {
            'viewer-subnet-overlap': ('viewer_subnet','172.30.242.0/29'),
            'viewer-subnet-public': ('viewer_subnet','8.8.8.0/30'),
            'viewer-address-outside': ('viewer_address','172.30.242.4'),
            'viewer-address-gateway': ('viewer_address','172.30.243.1'),
            'viewer-address-ipv6': ('viewer_address','::1'),
        }[case]
        config['scout_network'][key] = value
    path = tmp_path / 'invalid.yaml'
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match=r'^Invalid Scout operating settings\.$'):
        settings.load_settings(path)


def test_SR_012_topology_rejects_nonlocal_viewer_or_any_published_control():
    run = lifecycle.BrowserRun()
    for change in ('lan','missing','cdp','extra','browser','scout','proxy',
                   'restricted-external','viewer-internal','viewer-subnet','viewer-address','viewer-scout'):
        model = copy.deepcopy(run.model)
        port = model['services']['firewall']['ports'][0]
        if change == 'lan': port['host_ip'] = '0.0.0.0'
        elif change == 'missing': del model['services']['firewall']['ports']
        elif change == 'cdp': port['target'] = 9222
        elif change == 'extra': model['services']['firewall']['ports'].append(dict(port))
        elif change == 'restricted-external': model['networks']['restricted']['internal'] = False
        elif change == 'viewer-internal': model['networks']['viewer']['internal'] = True
        elif change == 'viewer-subnet': model['networks']['viewer']['ipam']['config'][0]['subnet'] = '172.30.244.0/30'
        elif change == 'viewer-address': model['services']['firewall']['networks']['viewer']['ipv4_address'] = '172.30.243.1'
        elif change == 'viewer-scout': model['services']['scout']['networks']['viewer'] = {}
        else: model['services'][change]['ports'] = [dict(port)]
        with pytest.raises(ValueError, match='Scout network configuration mismatch'):
            lifecycle.validate_topology(model, run.config)


def test_SR_011_production_headless_has_no_display_or_viewer(scout_stack):
    with scout_stack.browser_run() as run:
        assert run.config['browser']['mode'] == 'headless' and run.config['browser']['viewer'] is False
        with pytest.raises(RuntimeError, match='Browser viewer disabled'):
            run.viewer_access()
        lifecycle.checked(['docker','exec',run.container,'python3','-c',r'''
from pathlib import Path
import json
ready=json.loads(Path('/tmp/browser-ready.json').read_text())
assert b'--headless=new' in Path(f"/proc/{ready['pid']}/cmdline").read_bytes().split(b'\0')
assert b'DISPLAY=' not in Path(f"/proc/{ready['pid']}/environ").read_bytes()
assert not Path('/tmp/browser-viewer-password').exists() and not Path('/tmp/.X11-unix/X99').exists()
assert not any((p/'comm').read_text().strip() in {'Xvfb','x11vnc'} for p in Path('/proc').iterdir() if p.name.isdigit())
'''])
        probe(run, OBSERVE + r'''
with Browser() as browser, connect(os.environ['BROWSER_CDP_URL'],proxy=None,open_timeout=2) as observer:
    assert dimensions(attach(pages()[0]))==browser.settings['browser']['viewport']
''')
        with pytest.raises(OSError):
            http_viewer(run.config['scout_network']['viewer_port'])


def test_SR_011_headed_without_viewer_uses_only_container_display(scout_stack, tmp_path):
    with configured_mode(scout_stack,tmp_path/'no-viewer','headed',False) as run, run:
        lifecycle.checked(['docker','exec',run.container,'python3','-c',r'''
from pathlib import Path
import json
ready=json.loads(Path('/tmp/browser-ready.json').read_text())
assert b'--headless=new' not in Path(f"/proc/{ready['pid']}/cmdline").read_bytes().split(b'\0')
assert b'DISPLAY=:99' in Path(f"/proc/{ready['pid']}/environ").read_bytes().split(b'\0')
assert Path('/tmp/.X11-unix/X99').is_socket() and not Path('/tmp/browser-viewer-password').exists()
'''])
        probe(run,OBSERVE + r'''
with Browser() as browser, connect(os.environ['BROWSER_CDP_URL'],proxy=None,open_timeout=2) as observer:
    assert dimensions(attach(pages()[0]))==browser.settings['browser']['viewport']
''')
        with pytest.raises(RuntimeError,match='Browser viewer disabled'): run.viewer_access()
        with pytest.raises(OSError): http_viewer(run.config['scout_network']['viewer_port'])


def test_SR_012_real_viewer_authentication_rotation_and_no_lan_control(scout_stack, tmp_path):
    previous = None
    with configured_mode(scout_stack,tmp_path/'auth','headed',True):
        for _ in range(2):
            run = lifecycle.BrowserRun(compose_files=(lifecycle.COMPOSE,tmp_path/'auth/images.yaml'),
                                       config_path=tmp_path/'auth/config.yaml')
            with run:
                owned = run.container
                access = run.viewer_access()
                assert access['password'] != previous and len(access['password']) == 43
                assert access['password'] not in json.dumps(run.scout_environment())
                port = run.config['scout_network']['viewer_port']
                auth = 'Basic '+base64.b64encode(('scout:'+access['password']).encode()).decode()
                assert http_viewer(port)[0] == 401
                assert http_viewer(port,'Basic '+base64.b64encode(b'scout:wrong').decode())[0] == 401
                status, page = http_viewer(port,auth)
                assert status == 200 and b'noVNC' in page
                assert http_viewer(port,auth,host='untrusted.test:'+str(port))[0] == 403
                assert http_viewer(port,auth,origin='https://untrusted.test')[0] == 403
                for header in ({}, {'Authorization':auth,'Origin':'https://untrusted.test'}):
                    with pytest.raises(InvalidStatus) as error:
                        connect(f'ws://127.0.0.1:{port}/websockify',additional_headers=header,proxy=None,open_timeout=3)
                    assert error.value.response.status_code == (401 if not header else 403)
                if previous is not None:
                    stale = 'Basic '+base64.b64encode(('scout:'+previous).encode()).decode()
                    assert http_viewer(port,stale)[0] == 401
                previous = access['password']
                firewall=json.loads(lifecycle.checked(['docker','inspect',lifecycle.checked(run.compose+['ps','-q','firewall'])]))[0]
                assert firewall['HostConfig']['PortBindings']=={f'{port}/tcp':[{'HostIp':'127.0.0.1','HostPort':str(port)}]}
                assert firewall['NetworkSettings']['Ports']=={f'{port}/tcp':[{'HostIp':'127.0.0.1','HostPort':str(port)}]}
                assert not run.inspect_owned()['HostConfig']['PortBindings']
                probe(run, """
import socket
for address,port in (('172.30.243.2',6080),('172.30.242.4',9222)):
    try: socket.create_connection((address,port),timeout=1)
    except OSError: pass
    else: raise AssertionError('Scout reached viewer/raw CDP')
""")
                lifecycle.checked(['docker','exec',run.container,'python3','-c',r'''
from pathlib import Path
assert Path('/tmp/browser-viewer-password').stat().st_mode & 0o777 == 0o600
secret=Path('/tmp/browser-viewer-password').read_bytes()
assert secret not in Path('/tmp/browser-ready.json').read_bytes()
assert not any(secret in (p/'cmdline').read_bytes() for p in Path('/proc').iterdir() if p.name.isdigit())
assert not any(Path(p).exists() for p in ('/var/lib/scout','/opt/hermes','/var/run/docker.sock'))
'''])
            assert subprocess.run(['docker','inspect',owned],capture_output=True).returncode != 0


def test_SR_012_SR_013_real_noVNC_mouse_keys_bypass_review_and_stale_scout_action_rejects(headed_web, tmp_path):
    stack, _, _, _ = headed_web
    with stack.browser_run() as run:
        options = [value for key,setting in run.scout_environment().items() for value in ('-e',key+'='+setting)]
        code = SETUP + r'''
with Browser() as browser:
    adapter=browser.evidence();tab=adapter.tabs()[0]['target_id'];reviewer=Reviewer()
    gate=g.BrowserActionGate(browser,reviewer=reviewer,target_id=tab)
    successful(gate,'navigate',{'url':'http://public.test/gate'});wait_ready(browser)
    snapshot=successful(gate,'snapshot')['snapshot']
    points=browser.command('eval',"""(() => {
      const point=id=>{const r=document.querySelector('#'+id).getBoundingClientRect();
        return [screenX+(outerWidth-innerWidth)/2+r.x+r.width/2,
                screenY+outerHeight-innerHeight+r.y+r.height/2];};
      return {click:point('click'),query:point('query')};
    })()""")['result']
    print(json.dumps(points),flush=True);input()
    actual=browser.command('eval',"""(async () => {
      const deadline=Date.now()+10000;
      while(window.clicks!==1 || document.querySelector('#query').value!=='initialhuman') {
        if(Date.now()>deadline)break;await new Promise(resolve=>setTimeout(resolve,50));
      }
      return {clicks:window.clicks,query:document.querySelector('#query').value,
              focus:document.activeElement.id,innerWidth,innerHeight,outerWidth,outerHeight,screenX,screenY};
    })()""")['result']
    assert actual['clicks']==1 and actual['query']=='initialhuman',actual
    assert reviewer.calls==['navigate'], 'Human input entered Scout review'
    print('manual-ok',flush=True)
    def pending(_):
        print('review-wait',flush=True);input()
        deadline=time.monotonic()+10
        while browser.command('eval','window.clicks')['result']!=2:
            assert time.monotonic()<deadline,'Manual click did not arrive during review'
            time.sleep(.05)
    reviewer.callback=pending
    result=dispatch(gate,'keypress',{'key':'Enter'})
    assert result['status']=='rejected' and result['execution_started'] is False
    assert browser.command('eval','window.submits')['result']==0
    assert browser.command('eval','window.clicks')['result']==2
    print('stale-ok',flush=True);input()
    reviewer.callback=None
    successful(gate,'click',{'ref':reference(snapshot,'Controlled click')})
    assert browser.command('eval','window.clicks')['result']==3
    assert reviewer.calls==['navigate','keypress','click']
    print('done',flush=True)
'''
        child = subprocess.Popen(run.compose+['run','--rm','--no-deps','-T',*options,
            '--entrypoint','python','scout','-u','-c',code], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
        def line():
            with selectors.DefaultSelector() as ready:
                ready.register(child.stdout,selectors.EVENT_READ)
                assert ready.select(45), 'Controlled Scout/viewer synchronization timed out'
            value=child.stdout.readline().decode().strip()
            if not value:
                _, errors = child.communicate(timeout=30)
                pytest.fail('Controlled Scout fixture exited: ' + errors.decode()[-2000:])
            return value
        def resume():
            child.stdin.write(b'\n');child.stdin.flush()
        try:
            points=json.loads(line())
            with ViewerClient(tmp_path/'viewer-chrome',run.viewer_access()) as viewer:
                # Actual canvas contains VNC framebuffer, not merely HTML assets.
                frame = viewer.evaluate("""(async () => {
                    const deadline=Date.now()+15000;
                    while (true) {
                        const c=document.querySelector('canvas');let colored=0;
                        if(c.width && c.height) {
                            const pixels=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
                            for(let i=0;i<pixels.length;i+=4) if(pixels[i]+pixels[i+1]+pixels[i+2]>0)colored++;
                        }
                        if(c.width===1280 && c.height>=720 && colored>10000 || Date.now()>deadline)
                            return {width:c.width,height:c.height,colored};
                        await new Promise(resolve=>setTimeout(resolve,50));
                    }
                })()""")
                assert frame['width']==1280 and frame['height']>=720 and frame['colored']>10000, frame
                viewer.click(*points['click'])
                viewer.click(*points['query']);viewer.type('human')
                resume();assert line()=='manual-ok';assert line()=='review-wait'
                viewer.click(*points['click'])
                resume();assert line()=='stale-ok'
            # Viewer disconnect is independent of browser mission lifetime.
            assert run.inspect_owned()['State']['Running']
            resume();assert line()=='done'
            _, errors=child.communicate(timeout=30)
            assert child.returncode==0, errors[-2000:]
        finally:
            if child.poll() is None:
                child.terminate();child.communicate(timeout=30)


def test_SR_011_SR_001_SR_002_SR_019_headed_sandbox_public_web_and_network_denials(headed_web):
    boundary.test_SR_001_native_chromium_sandbox_and_anonymous_identity(headed_web)
    boundary.test_SR_019_public_http_https_tls_and_forbidden_main_redirects(headed_web)
    boundary.test_SR_002_background_http_websocket_cannot_control_cdp(headed_web)
    isolation.test_SR_004_SR_019_browser_direct_egress_and_unauthorized_control_denied(headed_web[0])


def test_SR_011_SR_006_SR_007_SR_008_SR_009_SR_010_headed_context_viewport_and_native_download_denial(headed_web):
    stack = headed_web[0]
    safeguards.test_SR_008_SR_009_main_window_tabs_share_contents_and_popup_stays_separate(stack)
    for kind in ('attachment','redirect','blob'):
        safeguards.test_SR_006_SR_010_native_download_attempts_cancel_after_startup_disconnect_and_media_loads(headed_web,kind)
