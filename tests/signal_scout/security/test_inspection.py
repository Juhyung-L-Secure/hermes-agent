import importlib

import pytest


SETUP = r'''
import importlib,json,time
Browser=importlib.import_module('plugins.signal-scout.scout_runtime.browser.owner').Browser
e=importlib.import_module('plugins.signal-scout.scout_runtime.browser.session')
def ref(snapshot,label):
    rows=[row for row in snapshot.splitlines() if json.dumps(label) in row and '[@s' in row]
    assert len(rows)==1,'Controlled reference missing or ambiguous'
    return rows[0].rsplit('[',1)[1][:-1]
def open_fixture(browser,path='/evidence'):
    browser.navigate('http://public.test'+path)
    deadline=time.monotonic()+browser.settings['browser']['command_timeout']
    while not browser.command('eval','window.fixtureReady===true')['result']:
        assert time.monotonic()<deadline,'Controlled fixture did not load'
'''


def test_AE_001_AE_002_compact_refs_scroll_current_point_and_no_effect(fixture_web):
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + r'''
with Browser() as browser:
    open_fixture(browser)
    adapter=browser.session();tab=adapter._tabs()[0]['target_id']
    snapshot=adapter._inspect_snapshot(tab)
    reference=ref(snapshot,'Below fold');binding=adapter._refs[reference]
    original=dict(adapter._refs)
    scan=adapter._frames(tab)
    adapter._scroll(binding,scan.frames)
    point=adapter._point(binding,scan.frames)
    assert point[0]>=0 and point[1]>=0 and browser.command('eval','scrollY')['result']>0
    assert browser.command('eval','window.counts.click')['result']==0
    assert browser.command('eval',"document.querySelector('#query').value")['result']=='initial'
    assert adapter._refs[ref(adapter._inspect_snapshot(tab),'Below fold')]==binding
    assert all(adapter._refs[key]==value for key,value in original.items())
    browser.command('eval',"document.querySelector('#low').replaceWith(document.querySelector('#low').cloneNode(true));true")
    try:
        adapter._run(lambda:adapter._scroll(binding,adapter._frames(tab).frames))
    except e.ActionRejected:
        pass
    else:
        raise AssertionError('Detached original node was accepted')
    assert not browser.failed and adapter._refs[reference]==binding
    assert ref(adapter._inspect_snapshot(tab),'Below fold')!=reference
''', timeout=150)


def test_AE_001_AE_002_AE_004_AE_005_same_cross_origin_points_and_focus(fixture_web):
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + r'''
with Browser() as browser:
    open_fixture(browser)
    adapter=browser.session();tab=adapter._tabs()[0]['target_id']
    snapshot=adapter._inspect_snapshot(tab)
    buttons=[row.rsplit('[',1)[1][:-1] for row in snapshot.splitlines() if '- button "Frame button" [@s' in row]
    assert len(buttons)==2
    targets=set()
    for reference in buttons:
        binding=adapter._refs[reference];targets.add(binding.target)
        frames=adapter._frames(tab).frames
        adapter._scroll(binding,frames)
        old=adapter._point(binding,frames)
        owner='same' if binding.target==tab else 'cross'
        browser.command('eval',"document.querySelector('#"+owner+"').style.position='relative';document.querySelector('#"+owner+"').style.left='40px';true")
        frames=adapter._frames(tab).frames
        current=adapter._point(binding,frames)
        assert current[0]>old[0]+30
        frame=frames[binding.frame]
        adapter._evaluate(frame,"document.querySelector('#childinput').focus();true")
        focus,chain=adapter._focus(tab,frames)
        assert focus.frame==binding.frame and focus.target==binding.target and len(chain)>=2
        assert browser.command('eval','document.activeElement.id')['result']==owner
    assert tab in targets and len(targets)==2
    open_fixture(browser,'/evidence-focus')
    adapter._inspect_snapshot(tab);frames=adapter._frames(tab).frames
    browser.command('eval','window.shadowInputs[0].focus();true')
    focus,chain=adapter._focus(tab,frames)
    assert focus==adapter._refs[ref(adapter._inspect_snapshot(tab),'Closed first')]
    assert chain[-1]==focus
''', timeout=180)


@pytest.mark.parametrize('message', ['No node with given id found','Could not find node with given id',
    'Cannot find context with specified id','No frame for given id found'])
def test_AE_006_expected_target_loss_protocol_errors_reject(message):
    module=importlib.import_module('plugins.signal-scout.scout_runtime.browser.session')
    from types import SimpleNamespace
    browser=SimpleNamespace(failed=False)
    adapter=module.BrowserSession(browser)
    def unavailable():
        raise module._ProtocolError({'code':-32000,'message':message})
    with pytest.raises(module.ActionRejected):
        adapter._run(unavailable)
    assert not browser.failed
