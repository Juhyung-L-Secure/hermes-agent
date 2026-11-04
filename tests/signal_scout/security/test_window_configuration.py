import subprocess

import yaml

from tests.signal_scout.support.stack import lifecycle
from tests.signal_scout.support.browser_policy import OBSERVE, settings


def test_SR_007_SR_008_SR_009_changed_configuration_reaches_real_window_and_scale(scout_stack, tmp_path):
    config = settings.load_settings(lifecycle.CONFIG)
    config['browser']['viewport'] = {'width':960, 'height':600, 'device_scale_factor':2}
    path = tmp_path / 'configured-window.yaml'
    path.write_text(yaml.safe_dump(config))
    with scout_stack.browser_run(config_path=path, restore_helpers=True) as run:
            code = OBSERVE + r'''
with Browser(config) as browser, connect(os.environ['BROWSER_CDP_URL'], proxy=None, open_timeout=2) as observer:
    expected = {'width':960, 'height':600, 'device_scale_factor':2}
    initial = pages()[0]
    main_window = cdp('Browser.getWindowForTarget', {'targetId':initial['targetId']})['windowId']
    assert dimensions(attach(initial)) == expected
    assert capture_size(attach(initial)) == (1920,1200)
    browser.command('tab', 'new', 'about:blank')
    normal = next(t for t in pages() if t['targetId']!=initial['targetId'])
    assert cdp('Browser.getWindowForTarget', {'targetId':normal['targetId']})['windowId'] == main_window
    assert dimensions(attach(normal)) == expected
    assert capture_size(attach(normal)) == (1920,1200)
    browser.command('eval', "(() => { const p=window.open('about:blank','scale-popup','popup,width=540,height=360'); p.createdSize={width:p.innerWidth,height:p.innerHeight,device_scale_factor:p.devicePixelRatio}; return true; })()")
    popup = next(t for t in pages() if t['targetId'] not in {initial['targetId'],normal['targetId']})
    session = attach(popup)
    actual = dimensions(session)
    created = cdp('Runtime.evaluate', {'expression':'window.createdSize','returnByValue':True},session)['result']['value']
    assert actual == created and actual != expected, (created,actual)
    assert cdp('Browser.getWindowForTarget', {'targetId':popup['targetId']})['windowId'] != main_window
    assert actual['device_scale_factor'] == 2
    assert capture_size(session) == (actual['width']*2,actual['height']*2)
    assert dimensions(attach(initial)) == expected
    assert cdp('Target.getBrowserContexts')['browserContextIds'] == []
    assert len({t['browserContextId'] for t in pages()}) == 1
'''
            env = [value for key,setting in run.scout_environment().items() for value in ('-e',key+'='+setting)]
            result = subprocess.run(run.compose+['run','--rm','--no-deps','-T',*env,'--entrypoint','python','scout','-c',code],
                                    capture_output=True, text=True, timeout=90)
            assert result.returncode == 0, result.stderr[-4000:]
            assert result.stdout.strip() == ''
