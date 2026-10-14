"""SR_007–SR_009: changed baked settings reach the existing runtime stack."""

import subprocess
import uuid

import yaml

from tests.signal_scout.conftest import lifecycle
from tests.signal_scout.security.test_browser_safeguards import OBSERVE, settings


def test_SR_007_SR_008_SR_009_changed_configuration_reaches_real_window_and_scale(scout_stack, tmp_path):
    config = settings.load_settings(lifecycle.CONFIG)
    config['browser']['viewport'] = {'width':960, 'height':600, 'device_scale_factor':2}
    path = tmp_path / 'configured-window.yaml'
    path.write_text(yaml.safe_dump(config))
    # Bake changed settings into variants of the existing services, with the
    # same project, namespaces, entrypoints and volumes; no separate stack.
    image_names, overrides = [], {}
    prefix = 'signal-scout-window-fixture-' + uuid.uuid4().hex
    override = tmp_path / 'configured-images.yaml'
    try:
        for target, services in (('runtime',('scout',)), ('browser',('browser',)), ('network',('proxy','firewall'))):
            dockerfile = tmp_path / ('Dockerfile.' + target)
            dockerfile.write_text('FROM signal-scout-bootstrap-' + target + ':local\n'
                                 'COPY configured-window.yaml /opt/scout/config.yaml\n')
            image = prefix + '-' + target + ':local'
            lifecycle.checked(['docker','build','-q','-t',image,'-f',str(dockerfile),str(tmp_path)])
            image_names.append(image)
            for service in services:
                overrides[service] = {'image':image}
        override.write_text(yaml.safe_dump({'services':overrides}))
        run = scout_stack.browser_run(compose_files=(lifecycle.COMPOSE, override), config_path=path)
        with run:
            code = OBSERVE + r'''
with Browser() as browser, connect(os.environ['BROWSER_CDP_URL'], proxy=None, open_timeout=2) as observer:
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
    finally:
        lifecycle.BrowserRun().start_helpers()
        for image in image_names:
            lifecycle.checked(['docker','image','rm',image])
