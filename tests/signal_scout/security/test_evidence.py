"""AE_001–AE_006: real owned Chromium evidence, identity and context checks."""

import importlib
from types import SimpleNamespace

import pytest

from tests.signal_scout.security.test_browser_boundary import fixture_web
from tests.signal_scout.conftest import lifecycle

SETUP = r'''
import base64, importlib, json, struct, time
Browser = importlib.import_module('plugins.signal-scout.security.browser').Browser
e = importlib.import_module('plugins.signal-scout.security.evidence')
def ref(snapshot, label):
    lines=[line for line in snapshot.splitlines() if json.dumps(label) in line and '[@s' in line]
    assert len(lines)==1, 'Fixture reference missing or ambiguous'
    return lines[0].rsplit('[',1)[1][:-1]
def prepared(adapter, tab, kind, action):
    return adapter.prepare(target_id=tab,action_type=kind,action=action,activity='Inspect controlled research controls')
def open_fixture(browser, path='/evidence'):
    browser.navigate('http://public.test'+path)
    deadline=time.monotonic()+browser.settings['browser']['command_timeout']
    while not browser.command('eval','window.fixtureReady===true')['result']:
        assert time.monotonic()<deadline, 'Controlled iframe fixtures did not load'
def rejected(adapter, item, kind, action):
    try: adapter.recheck(item,action_type=kind,action=action)
    except e.EvidenceRejected as error:
        assert str(error) in {'Relevant browser context changed.','Original browser document changed.',
          'Original browser target is unavailable.','Original browser target is stale.',
          'Target interaction point is hidden or covered.','Original browser target is hidden.',
          'Proposed browser action changed.'}
    else: raise AssertionError('Changed action unexpectedly passed')
    assert not adapter._browser.failed
'''


def test_AE_001_AE_002_AE_003_compact_refs_scroll_raw_evidence_and_no_action(fixture_web):
    """AE_005: oversized original target still has a checked visible point."""
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + r'''
with Browser() as browser:
    open_fixture(browser)
    adapter=browser.evidence()
    tabs=adapter.tabs(); assert len(tabs)==1
    tab=tabs[0]['target_id']
    snapshot=adapter.snapshot(tab)
    assert '- button "Below fold" [@s' in snapshot
    assert '<html' not in snapshot and 'backendNodeId' not in snapshot
    assert adapter.snapshot(tab)==snapshot
    action={'ref':ref(snapshot,'Below fold')}
    before=browser.command('eval','({...window.counts,focus:document.activeElement.localName})')['result']
    packet=prepared(adapter,tab,'click',action)
    assert browser.command('eval','scrollY')['result']>0
    assert browser.command('eval','({...window.counts,focus:document.activeElement.localName})')['result']==before
    inputs=packet.reviewer_input()
    assert inputs['action']==action and inputs['action_type']=='click'
    assert inputs['context']['target']['target']['text']=='Below fold'
    assert struct.unpack('>II',inputs['screenshot'][16:24])==(1280,720)
    assert inputs['screenshot_mime']=='image/png'
    assert set(inputs)=={'screenshot','screenshot_mime','activity','action_type','action','context'}
    assert 'backendNodeId' not in json.dumps(inputs['context'])
    assert 'Below fold' not in repr(packet)
    reviewer=importlib.import_module('plugins.signal-scout.security.reviewer')
    calls=[]
    def offline_attempt(settings,policy,evidence,deadline,completed):
        calls.append((policy,evidence))
        completed.put(('deny',None,{},time.monotonic()))
    reviewer._attempt=offline_attempt
    verdict=reviewer.SafetyReviewer().review(**inputs)
    assert verdict.verdict=='deny' and verdict.attempts==1
    assert calls[0][0]==reviewer.load_policy()
    assert json.loads(calls[0][1][0]['text'])['action']==action
    assert base64.b64decode(calls[0][1][1]['image_url']['url'].split(',',1)[1])==inputs['screenshot']
    assert adapter.recheck(packet,action_type='click',action=action).interaction_point
    for reference,target in (('@missing',tab),(action['ref'],'missing-target')):
        try: prepared(adapter,target,'click',{'ref':reference})
        except e.EvidenceRejected: assert not browser.failed
        else: raise AssertionError('Missing exact reference/target unexpectedly passed')
    action={'ref':ref(snapshot,'Query'),'text':'exact typed fixture-secret'}
    packet=prepared(adapter,tab,'type',action)
    inputs=packet.reviewer_input()
    assert inputs['action']['text']=='exact typed fixture-secret'
    form=inputs['context']['target']['form']
    assert form['action']=='http://public.test/search' and form['method']=='get'
    assert any(c['value']=='fixture-secret' for c in form['controls'])
    assert 'fixture-secret' not in repr(packet)
    assert browser.command('eval',"document.querySelector('#query').value")['result']=='initial'
    assert browser.command('eval','window.counts')['result']=={'click':0,'input':0,'key':0,'submit':0,'focus':0}
    inputs['action']['text']='mutated caller copy'
    assert adapter.recheck(packet,action_type='type',action=action).interaction_point
    action={'ref':ref(snapshot,'Oversized')}
    packet=prepared(adapter,tab,'click',action)
    assert adapter.recheck(packet,action_type='click',action=action).interaction_point
''', timeout=150)


def test_AE_001_AE_002_AE_004_AE_005_same_and_cross_origin_frames(fixture_web):
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + r'''
with Browser() as browser:
    open_fixture(browser)
    adapter=browser.evidence(); tab=adapter.tabs()[0]['target_id']
    snapshot=adapter.snapshot(tab)
    frame_refs=[line.rsplit('[',1)[1][:-1] for line in snapshot.splitlines() if '- button "Frame button" [@s' in line]
    assert len(frame_refs)==2, len(frame_refs)
    frame_targets=set()
    for reference in frame_refs:
        binding=adapter._refs[reference]; frame_targets.add(binding.target)
        action={'ref':reference}; item=prepared(adapter,tab,'click',action)
        checked=adapter.recheck(item,action_type='click',action=action)
        assert checked._binding==binding and checked.interaction_point
        frame=adapter._frames(tab)[binding.frame]
        assert adapter._evaluate(frame,"document.querySelector('#child').dataset.clicked")['value']=='false'
        owner='same' if binding.target==tab else 'cross'
        old=checked.interaction_point
        browser.command('eval',"document.querySelector('#"+owner+"').style.position='relative';document.querySelector('#"+owner+"').style.left='40px';true")
        assert adapter.recheck(item,action_type='click',action=action).interaction_point[0]>old[0]+30
        browser.command('eval',"(() => { const cover=document.createElement('div');cover.id='cover';cover.style='position:fixed;inset:0;z-index:999';document.body.append(cover);return true; })()")
        rejected(adapter,item,'click',action)
        browser.command('eval',"document.querySelector('#cover').remove();true")
        browser.command('eval',"document.querySelector('#"+owner+"').style.display='none';true")
        rejected(adapter,item,'click',action)
        browser.command('eval',"document.querySelector('#"+owner+"').style.display='';true")
        adapter._evaluate(frame,"document.querySelector('#childinput').focus();true")
        focus_before=browser.command('eval','document.activeElement.id')['result']
        keys={'key':'Enter'}; key_item=prepared(adapter,tab,'keypress',keys)
        assert key_item.reviewer_input()['context']['target']['target']['attributes']['aria-label']=='Frame input'
        assert adapter.recheck(key_item,action_type='keypress',action=keys)._binding.frame==binding.frame
        assert browser.command('eval','document.activeElement.id')['result']==focus_before==owner
        browser.command('eval',"document.querySelector('#"+owner+"').addEventListener('load',function(){this.dataset.reloaded='yes'},{once:true});true")
        adapter._evaluate(frame,'location.reload();true')
        deadline=time.monotonic()+browser.settings['browser']['command_timeout']
        while not browser.command('eval',"document.querySelector('#"+owner+"').dataset.reloaded==='yes'")['result']:
            assert time.monotonic()<deadline, 'Controlled frame reload did not finish'
        rejected(adapter,item,'click',action)
    assert tab in frame_targets and len(frame_targets)==2, 'Cross-origin fixture did not exercise OOPIF'
    browser.command('eval',"document.querySelector('#same').contentDocument.querySelector('#childinput').focus(); true")
    item=prepared(adapter,tab,'keypress',{'key':'Enter'})
    assert item.reviewer_input()['context']['target']['target']['attributes']['aria-label']=='Frame input'
    assert adapter.recheck(item,action_type='keypress',action={'key':'Enter'}).interaction_point
''', timeout=150)


@pytest.mark.parametrize('change', ['replacement','text','destination','form','form-value','submit-base','focus','hidden','covered','document','action'])
def test_AE_004_AE_005_recheck_rejects_changed_identity_or_context(fixture_web, change):
    """AE_001: exact refs; AE_003: resolved submitter destination evidence."""
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + '\nchange='+repr(change)+r'''
with Browser() as browser:
    open_fixture(browser)
    adapter=browser.evidence(); tab=adapter.tabs()[0]['target_id']; snapshot=adapter.snapshot(tab)
    kind='keypress' if change=='focus' else 'click'
    target='Search' if change=='submit-base' else ('Query' if change.startswith('form') else 'Open research')
    action={'key':'Enter'} if kind=='keypress' else {'ref':ref(snapshot,target)}
    if kind=='keypress': browser.command('eval',"document.querySelector('#query').focus(); true")
    if change=='submit-base':
        browser.command('eval',"document.querySelector('#form').action='http://public.test/search';document.querySelector('#submit').formAction='/safe-submit';true")
    item=prepared(adapter,tab,kind,action)
    if change=='submit-base':
        details=item.reviewer_input()['context']['target']
        assert details['form']['action']=='http://public.test/search'
        assert details['target']['formOverrides']['action']=='http://public.test/safe-submit'
    scripts={
      'replacement':"document.querySelector('#link').replaceWith(document.querySelector('#link').cloneNode(true)); true",
      'text':"document.querySelector('#link').textContent='Buy now'; true",
      'destination':"document.querySelector('#link').href='/purchase'; true",
      'form':"document.querySelector('#form').action='/purchase'; true",
      'form-value':"document.querySelector('#password').value='changed fixture value'; true",
      'submit-base':"const base=document.createElement('base');base.href='http://v6.test/';document.head.append(base);true",
      'focus':"document.querySelector('#password').focus(); true",
      'hidden':"document.querySelector('#link').style.visibility='hidden'; true",
      'covered':"const cover=document.createElement('div');cover.style='position:fixed;inset:0;background:red;z-index:999';document.body.append(cover);true",
      'document':"location.reload(); true",
      'action':"true"}
    browser.command('eval',scripts[change])
    if change=='document':
        deadline=time.monotonic()+browser.settings['browser']['command_timeout']
        while not browser.command('eval','window.fixtureReady===true')['result']:
            assert time.monotonic()<deadline
    if change=='action': action={'ref':ref(snapshot,'Search')}
    rejected(adapter,item,kind,action)
    assert browser.command('eval','true')['result'] is True
    if change=='replacement':
        newer=adapter.snapshot(tab)
        assert ref(newer,'Open research')!=ref(snapshot,'Open research')
        try: prepared(adapter,tab,'click',{'ref':ref(snapshot,'Open research')})
        except e.EvidenceRejected: pass
        else: raise AssertionError('Old ref substituted lookalike')
''', timeout=150)


def test_AE_002_AE_004_AE_005_keys_navigation_movement_and_pixel_changes(fixture_web):
    stack, _, counts, _ = fixture_web
    stack.browser_probe(SETUP + r'''
with Browser() as browser:
    open_fixture(browser)
    adapter=browser.evidence(); tab=adapter.tabs()[0]['target_id']; snapshot=adapter.snapshot(tab)
    keys={'key':'PageDown'}; item=prepared(adapter,tab,'keypress',keys)
    assert item.reviewer_input()['context']['target'] is None
    assert adapter.recheck(item,action_type='keypress',action=keys).interaction_point is None
    nav={'url':'http://public.test/never-visited-by-preparation'}
    item=prepared(adapter,tab,'navigate',nav)
    assert item.reviewer_input()['action']==nav
    assert browser.command('eval','location.pathname')['result']=='/evidence'
    assert adapter.recheck(item,action_type='navigate',action=nav).interaction_point is None
    action={'ref':ref(snapshot,'Open research')}; item=prepared(adapter,tab,'click',action)
    old=item.reviewer_input()['context']['interaction_point']
    browser.command('eval',"document.querySelector('#link').style.position='relative';document.querySelector('#link').style.left='200px';document.body.style.background='lavender'; true")
    checked=adapter.recheck(item,action_type='click',action=action)
    assert checked.interaction_point[0]>old[0]+190
    assert checked._binding==item._binding
    assert browser.command('eval','window.counts')['result']=={'click':0,'input':0,'key':0,'submit':0,'focus':0}
''', timeout=150)


def test_AE_002_AE_003_AE_004_closed_shadow_keyboard_focus(fixture_web):
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + r'''
with Browser() as browser:
    open_fixture(browser,'/evidence-focus')
    browser.command('eval','window.shadowInputs[0].focus(); true')
    adapter=browser.evidence(); tab=adapter.tabs()[0]['target_id']; action={'key':'Enter'}
    assert browser.command('eval',"document.activeElement.id==='focus-host' && document.activeElement.shadowRoot===null")['result']
    item=prepared(adapter,tab,'keypress',action)
    assert item.reviewer_input()['context']['target']['target']['attributes']['aria-label']=='Closed first'
    assert adapter.recheck(item,action_type='keypress',action=action)._binding==item._binding
    assert browser.command('eval','window.keys')['result']==0
    browser.command('eval','window.shadowInputs[1].focus(); true')
    assert browser.command('eval',"document.activeElement.id==='focus-host' && window.shadowInputs[0].value===window.shadowInputs[1].value")['result']
    rejected(adapter,item,'keypress',action)
    assert browser.command('eval','window.keys')['result']==0
''', timeout=150)


def test_AE_003_AE_004_active_descendant_identity_and_semantics(fixture_web):
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + r'''
with Browser() as browser:
    adapter=browser.evidence(); tab=adapter.tabs()[0]['target_id']; action={'key':'Enter'}
    for change in ('option','replacement','text'):
        open_fixture(browser,'/evidence-focus')
        browser.command('eval',"document.querySelector('#combo').focus(); true")
        item=prepared(adapter,tab,'keypress',action)
        descendant=item.reviewer_input()['context']['focus']['accessibility']['properties']['activedescendant']
        assert descendant[0]['text']=='Research' and descendant[0]['attributes']['role']=='option'
        assert 'backendDOMNodeId' not in json.dumps(item.reviewer_input()['context'])
        assert adapter.recheck(item,action_type='keypress',action=action)._binding==item._binding
        scripts={'option':"document.querySelector('#combo').setAttribute('aria-activedescendant','choice-b'); true",
          'replacement':"document.querySelector('#choice-a').replaceWith(document.querySelector('#choice-a').cloneNode(true)); true",
          'text':"document.querySelector('#choice-a').textContent='Purchase'; true"}
        browser.command('eval',scripts[change])
        assert browser.command('eval',"document.activeElement.id==='combo' && document.activeElement.value==='unchanged'")['result']
        rejected(adapter,item,'keypress',action)
        assert browser.command('eval','window.keys')['result']==0
''', timeout=150)


def test_AE_003_AE_004_active_descendant_computed_accessible_semantics(fixture_web):
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + r'''
with Browser() as browser:
    adapter=browser.evidence(); tab=adapter.tabs()[0]['target_id']; action={'key':'Enter'}
    for attribute in ('label','description'):
        open_fixture(browser,'/evidence-focus')
        browser.command('eval',"document.querySelector('#combo').setAttribute('aria-activedescendant','choice-c'); document.querySelector('#combo').focus(); window.originalOption=document.querySelector('#choice-c'); true")
        markup=browser.command('eval','window.originalOption.outerHTML')['result']
        item=prepared(adapter,tab,'keypress',action)
        descendant=item.reviewer_input()['context']['focus']['accessibility']['properties']['activedescendant'][0]
        assert adapter.recheck(item,action_type='keypress',action=action)._binding==item._binding
        browser.command('eval',"document.querySelector('#external-"+attribute+"').textContent='Purchase'; true")
        assert browser.command('eval',"document.activeElement.id==='combo' && document.activeElement.value==='unchanged' && document.querySelector('#choice-c')===window.originalOption")['result']
        assert browser.command('eval','window.originalOption.outerHTML')['result']==markup
        _, current=adapter._context(tab,item._binding,adapter._frames(tab),keyboard=True)
        assert current['active_descendants']==item._baseline['active_descendants']
        rejected(adapter,item,'keypress',action)
        assert descendant['accessibility']['role']=='option'
        assert descendant['accessibility']['name']=='Research'
        assert descendant['accessibility']['description']=='Research details'
        assert 'backendDOMNodeId' not in json.dumps(item.reviewer_input()['context'])
        assert browser.command('eval','window.keys')['result']==0
''', timeout=150)


@pytest.mark.parametrize('message', ['Node is detached from document',
    'Frame with the given id was not found.','Node does not have a layout object'])
def test_AE_006_expected_target_loss_protocol_errors_reject(message):
    evidence = importlib.import_module('plugins.signal-scout.security.evidence')
    browser = SimpleNamespace(failed=False)
    adapter = evidence.BrowserEvidence(browser)
    def vanished():
        raise evidence._ProtocolError({'code':-32000,'message':message})
    with pytest.raises(evidence.EvidenceRejected, match='Original browser target is stale.'):
        adapter._run(vanished)
    assert not browser.failed


def test_AE_002_AE_005_AE_006_target_loss_during_inspection_rejects(fixture_web):
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + r'''
with Browser() as browser:
    adapter=browser.evidence(); tab=adapter.tabs()[0]['target_id']
    for loss in ('node','layout','frame'):
        open_fixture(browser)
        snapshot=adapter.snapshot(tab)
        frames=adapter._frames(tab)
        target=ref(snapshot,'Open research') if loss!='frame' else next(
          key for key,value in adapter._refs.items() if value.tab==tab and
          value.frame in frames and value.document==frames[value.frame].document and value.target!=tab)
        action={'ref':target}; command=adapter._cdp.command; observed=[]
        method_to_interrupt='DOM.getFrameOwner' if loss=='frame' else 'DOM.scrollIntoViewIfNeeded'
        def lose_target(method,params=None,session=None):
            if method==method_to_interrupt and not observed:
                script={'node':"document.querySelector('#link').remove(); true",
                  'layout':"document.querySelector('#link').style.display='none'; true",
                  'frame':"document.querySelector('#cross').remove(); true"}[loss]
                browser.command('eval',script)
                try: return command(method,params,session)
                except e._ProtocolError as error:
                    observed.append(error.stale)
                    raise
            return command(method,params,session)
        adapter._cdp.command=lose_target
        try:
            try: prepared(adapter,tab,'click',action)
            except e.EvidenceRejected as error: assert str(error)=='Original browser target is stale.'
            else: raise AssertionError('Target loss unexpectedly prepared')
        finally: adapter._cdp.command=command
        assert observed==[True] and not browser.failed
        assert browser.command('eval','true')['result'] is True
''', timeout=150)


def test_AE_003_raw_capture_bytes_and_popup_dimensions(fixture_web):
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + r'''
with Browser() as browser:
    open_fixture(browser)
    adapter=browser.evidence(); main=adapter.tabs()[0]['target_id']
    browser.command('eval',"window.open('http://public.test/evidence','evidence-popup','popup,width=540,height=360'); true")
    popup=next(t['target_id'] for t in adapter.tabs() if t['target_id']!=main)
    snapshot=adapter.snapshot(popup)
    adapter.snapshot(main) # Active native tab is irrelevant to explicit target ID.
    frame=next(f for f in adapter._frames(popup).values() if f.parent is None)
    before=adapter._evaluate(frame,'({width:innerWidth,height:innerHeight,scale:devicePixelRatio})')['value']
    captures=[]; command=adapter._cdp.command
    def capture(method,params=None,session=None):
        result=command(method,params,session)
        if method=='Page.captureScreenshot':
            assert params=={'format':'png','captureBeyondViewport':False}
            captures.append(base64.b64decode(result['data']))
        return result
    adapter._cdp.command=capture
    item=prepared(adapter,popup,'click',{'ref':ref(snapshot,'Open research')})
    inputs=item.reviewer_input()
    assert inputs['context']['viewport']==before
    assert (before['width'],before['height'])!=(1280,720)
    assert inputs['screenshot']==captures[0]
    assert struct.unpack('>II',inputs['screenshot'][16:24])==(round(before['width']*before['scale']),round(before['height']*before['scale']))
    assert adapter._evaluate(frame,'({width:innerWidth,height:innerHeight,scale:devicePixelRatio})')['value']==before
''', timeout=150)


@pytest.mark.parametrize('phase', ['capture','check'])
def test_AE_006_required_machinery_failure_poison_and_owned_teardown(fixture_web, phase):
    stack, _, _, _ = fixture_web
    helper=stack.compose('ps','-q','proxy')
    with pytest.raises(AssertionError,match='Required browser evidence check failed.'):
        with stack.browser_run() as run:
            stack.browser_probe(SETUP + '\nphase='+repr(phase)+r'''
with Browser() as browser:
    open_fixture(browser)
    adapter=browser.evidence(); tab=adapter.tabs()[0]['target_id']; snapshot=adapter.snapshot(tab)
    action={'ref':ref(snapshot,'Open research')}
    if phase=='check': item=prepared(adapter,tab,'click',action)
    native=adapter._cdp.command
    def broken(method,params=None,session=None):
        if method==('Page.captureScreenshot' if phase=='capture' else 'DOM.getNodeForLocation'):
            return native('Scout.requiredMachineryDoesNotExist',{},session)
        return native(method,params,session)
    adapter._cdp.command=broken
    try:
        if phase=='capture': prepared(adapter,tab,'click',action)
        else: adapter.recheck(item,action_type='click',action=action)
    except e.EvidenceFailure as error:
        assert str(error)=='Required browser evidence check failed.'
        assert browser.failed
        try: browser.command('eval','true')
        except RuntimeError as unavailable: assert str(unavailable)=='Browser control unavailable.'
        else: raise AssertionError('Failed browser stayed usable')
        raise
    else: raise AssertionError('Required machinery failure did not propagate')
''',run=run, timeout=150)
    assert run.container is None and run.lock is None
    assert not lifecycle.checked(['docker','ps','-aq','--filter','label='+lifecycle.RUN_LABEL+'='+run.run])
    assert stack.compose('ps','-q','proxy')==helper


def test_RV_007_policy_packaged_read_only(scout_stack):
    scout_stack.probe('''
from pathlib import Path
path=Path('/opt/hermes/plugins/signal-scout/security/reviewer-policy.md')
assert path.stat().st_uid==path.stat().st_gid==0
assert path.stat().st_mode & 0o777 == 0o444
assert path.read_text().strip()
try: path.write_text('override')
except OSError as error: assert error.errno in (13,30)
else: raise AssertionError('Policy writable')
''')
