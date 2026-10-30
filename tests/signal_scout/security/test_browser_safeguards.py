"""SR_006–SR_010: shared download policy and real window/scale behavior."""

from tests.signal_scout.support.browser_policy import native_policy_variant, OBSERVE

import importlib

import pytest
import yaml

from tests.signal_scout.support.stack import lifecycle

settings = importlib.import_module('plugins.signal-scout.scout_runtime.settings')
POLICY = '/etc/opt/chrome_for_testing/policies/managed/scout.json'


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
    assert_SR_008_SR_009_main_window_tabs_share_contents_and_popup_stays_separate(scout_stack)


@pytest.mark.parametrize('kind', ['attachment', 'redirect', 'blob'])
def test_SR_006_SR_010_native_download_attempts_cancel_after_startup_disconnect_and_media_loads(fixture_web, kind):
    assert_SR_006_SR_010_native_download_attempts_cancel_after_startup_disconnect_and_media_loads(fixture_web, kind)


def test_SR_010_setup_protocol_failure_prevents_readiness_and_cleans_owner(scout_stack, tmp_path):
    code = r'''
import asyncio, json
import scout_runtime.browser.chromium as chromium
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
    entrypoint = ['python3', '-c', "import scout_runtime.browser.chromium as c\n"
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

from tests.signal_scout.support.browser_policy import assert_SR_008_SR_009_main_window_tabs_share_contents_and_popup_stays_separate, assert_SR_006_SR_010_native_download_attempts_cancel_after_startup_disconnect_and_media_loads
