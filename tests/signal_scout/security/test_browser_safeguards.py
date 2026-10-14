"""SR_006–SR_010: shared download policy and real window/scale behavior."""

import importlib
from contextlib import contextmanager
import json
import subprocess
import uuid

import pytest
import yaml

from tests.signal_scout.conftest import lifecycle
from tests.signal_scout.security.test_browser_boundary import fixture_web

settings = importlib.import_module('plugins.signal-scout.security.settings')
POLICY = '/etc/opt/chrome_for_testing/policies/managed/scout.json'


def assert_startup_disconnected(run):
    """Prove launcher owns no established CDP socket after publishing readiness."""
    lifecycle.checked(['docker', 'exec', run.container, 'python3', '-c', r'''
import json, os
from pathlib import Path
ready=json.loads(Path('/tmp/browser-ready.json').read_text())
parent=int(next(row.split()[1] for row in Path(f"/proc/{ready['pid']}/status").read_text().splitlines() if row.startswith('PPid:')))
inodes={os.readlink(fd)[8:-1] for fd in Path(f'/proc/{parent}/fd').iterdir() if os.readlink(fd).startswith('socket:[')}
rows=[row.split() for row in Path('/proc/net/tcp').read_text().splitlines()[1:]]
assert not [row for row in rows if row[2]=='0100007F:2406' and row[3]=='01' and row[9] in inodes]
policy=Path('/etc/opt/chrome_for_testing/policies/managed/scout.json')
assert policy.stat().st_uid==policy.stat().st_gid==0
assert policy.stat().st_mode & 0o777 == 0o444
assert json.loads(policy.read_text())=={'DownloadRestrictions':3}
'''])


@contextmanager
def native_policy_variant(stack, tmp_path, content, destination=POLICY, entrypoint=None):
    """Bake controlled policy defects into transient existing-service images only."""
    image = 'signal-scout-policy-fixture-' + uuid.uuid4().hex + ':local'
    dockerfile = tmp_path / 'Dockerfile.policy'
    source = 'FROM signal-scout-bootstrap-browser:local\nUSER 0:0\nRUN rm ' + POLICY + '\n'
    if content is not None:
        (tmp_path / 'policy.json').write_text(content)
        source += 'RUN install -d -m 0755 ' + destination.rsplit('/', 1)[0] + '\n'
        source += 'COPY --chown=0:0 --chmod=0444 policy.json ' + destination + '\n'
    dockerfile.write_text(source + 'USER 10000:10000\n')
    lifecycle.checked(['docker', 'build', '-q', '-t', image, '-f', str(dockerfile), str(tmp_path)])
    try:
        service = {'image':image}
        if entrypoint is not None:
            service['entrypoint'] = entrypoint
        override = tmp_path / 'policy-variant.yaml'
        override.write_text(yaml.safe_dump({'services': {'browser':service}}))
        yield stack.browser_run(compose_files=(*stack.browser_compose_files, override))
    finally:
        lifecycle.checked(['docker', 'image', 'rm', image])

# Observation uses CDP; browsing still uses the pinned native Hermes driver.
OBSERVE = r'''
import base64, importlib, json, os, struct, time
from websockets.sync.client import connect
Browser = importlib.import_module('plugins.signal-scout.security.browser').Browser
sequence, events = 0, []
def cdp(method, params=None, session=None):
    global sequence
    sequence += 1
    request = {'id':sequence, 'method':method, 'params':params or {}}
    if session is not None:
        request['sessionId'] = session
    observer.send(json.dumps(request))
    deadline = time.monotonic() + 10
    while True:
        response = json.loads(observer.recv(timeout=max(0.01, deadline-time.monotonic())))
        if response.get('id') == sequence:
            assert 'error' not in response, response
            return response['result']
        events.append(response)
def pages():
    return [t for t in cdp('Target.getTargets')['targetInfos'] if t['type']=='page']
def attach(target):
    return cdp('Target.attachToTarget', {'targetId':target['targetId'], 'flatten':True})['sessionId']
def dimensions(session):
    return cdp('Runtime.evaluate', {'expression':'({width:innerWidth,height:innerHeight,device_scale_factor:devicePixelRatio})',
                                  'returnByValue':True}, session)['result']['value']
def capture_size(session):
    png = base64.b64decode(cdp('Page.captureScreenshot', {'format':'png', 'captureBeyondViewport':False}, session)['data'])
    assert png[:8] == bytes([137,80,78,71,13,10,26,10])
    return struct.unpack('>II', png[16:24])
def download_records(session):
    result=cdp('Runtime.evaluate', {'expression':"""JSON.stringify(
      document.querySelector('downloads-manager')?.items_?.map(
        item=>({id:item.id,name:item.fileName,state:item.state})))""",
      'returnByValue':True}, session)
    assert 'exceptionDetails' not in result, result
    value=result['result'].get('value')
    return json.loads(value) if value else []
'''


@pytest.mark.parametrize('case', [
    'missing-viewport', 'missing-width', 'missing-height', 'missing-scale',
    'empty', 'extra', 'bool-width', 'float-height', 'zero-width', 'negative-height',
    'overflow-width', 'zero-scale', 'bool-scale', 'text-scale', 'nan-scale', 'infinite-scale', 'overflow-scale',
])
def test_SR_007_required_window_configuration_fails_explicitly(tmp_path, case):
    config = settings.load_settings(lifecycle.CONFIG)
    viewport = config['browser']['viewport']
    if case == 'missing-viewport':
        del config['browser']['viewport']
    elif case.startswith('missing-'):
        del viewport[{'missing-width':'width', 'missing-height':'height', 'missing-scale':'device_scale_factor'}[case]]
    elif case == 'empty':
        config['browser']['viewport'] = {}
    elif case == 'extra':
        viewport['allow_downloads'] = True
    else:
        key, value = {
            'bool-width':('width', True), 'float-height':('height', 720.5),
            'zero-width':('width', 0), 'negative-height':('height', -1),
            'overflow-width':('width', 2**31), 'zero-scale':('device_scale_factor', 0),
            'bool-scale':('device_scale_factor', True), 'text-scale':('device_scale_factor', '1'),
            'nan-scale':('device_scale_factor', float('nan')),
            'infinite-scale':('device_scale_factor', float('inf')),
            'overflow-scale':('device_scale_factor', 10**400),
        }[case]
        viewport[key] = value
    path = tmp_path / 'invalid-window.yaml'
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match=r'^Invalid Scout operating settings\.$'):
        settings.load_settings(path)


def test_SR_008_SR_009_main_window_tabs_share_contents_and_popup_stays_separate(scout_stack):
    scout_stack.browser_probe(OBSERVE + r'''
with Browser() as browser, connect(os.environ['BROWSER_CDP_URL'], proxy=None, open_timeout=2) as observer:
    viewport = browser.settings['browser']['viewport']
    initial = pages()[0]
    main_window = cdp('Browser.getWindowForTarget', {'targetId':initial['targetId']})['windowId']
    expected_pixels = (round(viewport['width']*viewport['device_scale_factor']),
                       round(viewport['height']*viewport['device_scale_factor']))
    assert dimensions(attach(initial)) == viewport
    assert capture_size(attach(initial)) == expected_pixels
    browser.command('tab', 'new', 'about:blank')
    normal = next(t for t in pages() if t['targetId'] != initial['targetId'])
    assert cdp('Browser.getWindowForTarget', {'targetId':normal['targetId']})['windowId'] == main_window
    assert dimensions(attach(normal)) == viewport
    assert capture_size(attach(normal)) == expected_pixels
    browser.command('eval', "(() => { const p=window.open('about:blank', 'sizing-popup', 'popup,width=540,height=360'); p.createdSize={width:p.innerWidth,height:p.innerHeight,device_scale_factor:p.devicePixelRatio}; return true; })()")
    popup = next(t for t in pages() if t['targetId'] not in {initial['targetId'], normal['targetId']})
    popup_window = cdp('Browser.getWindowForTarget', {'targetId':popup['targetId']})['windowId']
    assert popup_window != main_window
    popup_session = attach(popup)
    actual = dimensions(popup_session)
    created = cdp('Runtime.evaluate', {'expression':'window.createdSize','returnByValue':True},popup_session)['result']['value']
    assert actual == created and actual != viewport, (created,actual)
    assert actual['device_scale_factor'] == viewport['device_scale_factor']
    assert capture_size(popup_session) == (round(actual['width']*viewport['device_scale_factor']), round(actual['height']*viewport['device_scale_factor']))
    assert dimensions(attach(initial)) == viewport
    assert cdp('Target.getBrowserContexts')['browserContextIds'] == []
    assert len({t['browserContextId'] for t in pages()}) == 1
''')


@pytest.mark.parametrize('kind', ['attachment', 'redirect', 'blob'])
def test_SR_006_SR_010_native_download_attempts_cancel_after_startup_disconnect_and_media_loads(fixture_web, kind):
    stack, _, _, _ = fixture_web
    with stack.browser_run() as run:
        assert_startup_disconnected(run)
        result = stack.browser_probe(OBSERVE + '\nkind = ' + repr(kind) + r'''
with Browser() as browser, connect(os.environ['BROWSER_CDP_URL'], proxy=None, open_timeout=2) as observer:
    browser.navigate('http://public.test/safeguards')
    main = pages()[0]
    main_session = attach(main)
    media = cdp('Runtime.evaluate', {'expression':"""(async () => {
      const image=document.querySelector('#image'), audio=document.querySelector('#audio');
      const canvas=document.createElement('canvas'); canvas.width=16; canvas.height=16;
      const stream=canvas.captureStream(0), recorder=new MediaRecorder(stream,{mimeType:'video/webm;codecs=vp8'});
      const chunks=[]; recorder.ondataavailable=e=>chunks.push(e.data);
      const stopped=new Promise(resolve=>recorder.onstop=resolve); recorder.start();
      canvas.getContext('2d').fillRect(0,0,16,16); stream.getVideoTracks()[0].requestFrame();
      await new Promise(resolve=>setTimeout(resolve,200)); recorder.stop(); await stopped;
      const video=document.createElement('video'); video.src=URL.createObjectURL(new Blob(chunks,{type:'video/webm'}));
      document.body.append(video); video.load();
      const deadline=Date.now()+5000;
      while (!image.complete || image.naturalWidth!==1 || audio.readyState<1 || video.readyState<1) {
        if (Date.now()>deadline) throw new Error('Ordinary media did not load');
        await new Promise(resolve=>setTimeout(resolve,20));
      }
      return [image.naturalWidth,audio.duration,video.videoWidth,video.videoHeight];
    })()""", 'returnByValue':True, 'awaitPromise':True}, main_session)
    assert 'exceptionDetails' not in media, media
    assert media['result']['value'][0] == 1 and media['result']['value'][1] > 0
    assert media['result']['value'][2:] == [16,16]
    browser.command('tab', 'new', 'http://public.test/safeguards?tab')
    normal = next(t for t in pages() if t['targetId']!=main['targetId'])
    browser.command('eval', "window.open('http://public.test/safeguards?popup','download-popup','popup,width=540,height=360'); true")
    popup = next(t for t in pages() if t['targetId'] not in {main['targetId'], normal['targetId']})
    assert cdp('Target.getBrowserContexts')['browserContextIds'] == []
    assert len({t['browserContextId'] for t in pages()}) == 1
    for target in (main, normal, popup):
        session = attach(target)
        cdp('Page.enable', session=session)
        events.clear()
        filename = 'fixture-download-' + kind + '-' + target['targetId'] + '.txt'
        destination = 'http://public.test/attachment/' + filename
        if kind == 'redirect':
            destination = 'http://public.test/redirect/' + destination
        script = """(() => {
          const anchor=document.createElement('a'); anchor.download=FILENAME;
          anchor.href=DESTINATION; document.body.append(anchor); anchor.click(); return true;
        })()""".replace('FILENAME',json.dumps(filename)).replace('DESTINATION',
                    "URL.createObjectURL(new Blob(['download fixture'],{type:'application/octet-stream'}))"
                    if kind=='blob' else json.dumps(destination))
        assert cdp('Runtime.evaluate', {'expression':script, 'returnByValue':True, 'userGesture':True},session)['result']['value'] is True
        began, canceled = {}, set()
        deadline = time.monotonic()+10
        while not began or canceled != set(began):
            message = events.pop(0) if events else json.loads(observer.recv(timeout=max(0.01,deadline-time.monotonic())))
            method, params = message.get('method'), message.get('params',{})
            if message.get('sessionId') != session:
                continue
            if method == 'Page.downloadWillBegin':
                assert params['suggestedFilename'] == filename, params
                began[params['guid']] = params['url']
            elif method == 'Page.downloadProgress':
                assert params['state'] != 'completed', params
                if params['state']=='canceled':
                    canceled.add(params['guid'])
            assert time.monotonic()<deadline, 'Attempted download did not reach completed denial'
        assert len(began)==1
        assert next(iter(began.values())).startswith('blob:' if kind=='blob' else 'http://public.test/attachment/')
        assert dimensions(session)['device_scale_factor']==1
    assert browser.command('eval','true')['result'] is True
''', run=run, timeout=120)
        assert result == ''
        subprocess.run(['docker','exec',run.container,'python3','-c',
            "from pathlib import Path; assert not [p for p in Path('/tmp').rglob('*') "
            "if p.name.startswith('fixture-download-') or p.suffix=='.crdownload']"], check=True, timeout=10)


def test_SR_010_setup_protocol_failure_prevents_readiness_and_cleans_owner(scout_stack, tmp_path):
    code = r'''
import asyncio, json
import security.chromium as chromium
original = chromium.initialize_safeguards
class BrokenCommand:
    def __init__(self, connection): self.connection=connection
    async def send(self, raw):
        message=json.loads(raw)
        if message['method']=='Browser.setContentsSize':
            message['params']['windowId']=-1
        await self.connection.send(json.dumps(message))
    async def recv(self): return await self.connection.recv()
async def setup(connection, config):
    await original(BrokenCommand(connection),config)
chromium.initialize_safeguards=setup
raise SystemExit(chromium.main())
'''
    override = tmp_path / 'failed-safeguard.yaml'
    override.write_text(yaml.safe_dump({'services': {'browser': {'entrypoint':['python3','-c',code]}}}))
    run = scout_stack.browser_run(compose_files=(*scout_stack.browser_compose_files, override))
    with pytest.raises(RuntimeError, match='Browser startup failed'):
        with run:
            pytest.fail('Failed safeguard setup published readiness')
    assert run.container is None and run.lock is None
    assert not lifecycle.checked(['docker','ps','-aq','--filter','label='+lifecycle.RUN_LABEL+'='+run.run])


@pytest.mark.parametrize('case', ['missing', 'malformed', 'invalid', 'unapplied'])
def test_SR_010_missing_invalid_or_unapplied_native_policy_prevents_readiness_and_cleans_owner(scout_stack, tmp_path, case):
    content = {'missing':None, 'malformed':'{', 'invalid':'{"DownloadRestrictions":"3"}',
               'unapplied':'{"DownloadRestrictions":3}'}[case]
    destination = '/etc/opt/chrome/policies/managed/scout.json' if case=='unapplied' else POLICY
    with native_policy_variant(scout_stack, tmp_path, content, destination) as run:
        with pytest.raises(RuntimeError, match='Browser startup failed'):
            with run:
                pytest.fail('Unverified native policy published readiness')
        assert run.container is None and run.lock is None
        assert not lifecycle.checked(['docker','ps','-aq','--filter','label='+lifecycle.RUN_LABEL+'='+run.run])


@pytest.mark.parametrize('kind', ['attachment', 'redirect'])
def test_SR_006_policy_free_positive_control_enters_native_download_and_cancels_before_payload(fixture_web, tmp_path, kind):
    stack, _, _, _ = fixture_web
    # Only this transient test image omits policy. Skip only its startup policy
    # attestation; production has no switch, and real window/ownership setup stays.
    entrypoint = ['python3', '-c', "import security.chromium as c\n"
                  "async def policy_free(*_): pass\n"
                  "c.verify_download_policy=policy_free\nraise SystemExit(c.main())"]
    with native_policy_variant(stack, tmp_path, None, entrypoint=entrypoint) as run:
        with run:
            stack.browser_probe(OBSERVE + '\nkind = ' + repr(kind) + r'''
with Browser() as browser, connect(os.environ['BROWSER_CDP_URL'], proxy=None, open_timeout=2) as observer:
    browser.navigate('http://public.test/safeguards')
    main=pages()[0]
    browser.command('tab','new','http://public.test/safeguards?control-tab')
    normal=next(t for t in pages() if t['targetId']!=main['targetId'])
    browser.command('eval',"window.open('http://public.test/safeguards?control-popup','control-popup','popup,width=540,height=360'); true")
    popup=next(t for t in pages() if t['targetId'] not in {main['targetId'],normal['targetId']})
    record_target=cdp('Target.createTarget',{'url':'chrome://downloads'})['targetId']
    record_session=attach({'targetId':record_target})
    for target in (main,normal,popup):
        session=attach(target)
        cdp('Page.enable',session=session)
        events.clear()
        filename='fixture-control-'+kind+'-'+target['targetId']+'.txt'
        destination='http://public.test/attachment/'+filename
        if kind=='redirect': destination='http://public.test/redirect/'+destination
        script="""(() => { const a=document.createElement('a'); a.download=FILENAME;
          a.href=DESTINATION; document.body.append(a); a.click(); return true; })()"""
        script=script.replace('FILENAME',json.dumps(filename)).replace('DESTINATION',json.dumps(destination))
        assert cdp('Runtime.evaluate',{'expression':script,'userGesture':True,'returnByValue':True},session)['result']['value'] is True
        deadline=time.monotonic()+10
        while True:
            message=events.pop(0) if events else json.loads(observer.recv(timeout=max(0.01,deadline-time.monotonic())))
            if message.get('sessionId')==session and message.get('method')=='Page.downloadWillBegin':
                assert message['params']['suggestedFilename']==filename
                break
            assert time.monotonic()<deadline
        deadline=time.monotonic()+5
        while not (record:=next((item for item in download_records(record_session) if item['name']==filename),None)):
            assert time.monotonic()<deadline, 'Policy-free download missing'
            time.sleep(0.05)
        assert record['state']==0, record
        canceled=cdp('Runtime.evaluate',{'expression':"document.querySelector('downloads-manager').browserProxy_.handler.cancel("+json.dumps(record['id'])+"); true",
                      'returnByValue':True},record_session)
        assert 'exceptionDetails' not in canceled and canceled['result'].get('value') is True, canceled
        while next(item for item in download_records(record_session) if item['id']==record['id'])['state']!=1:
            assert time.monotonic()<deadline, 'Native cancellation did not finish'
            time.sleep(0.05)
    assert browser.command('eval','true')['result'] is True
''',run=run,timeout=90)
            lifecycle.checked(['docker','exec',run.container,'python3','-c',
                "from pathlib import Path; assert not [p for p in Path('/tmp').rglob('*') "
                "if p.name.startswith('fixture-control-') or p.suffix=='.crdownload']"])
