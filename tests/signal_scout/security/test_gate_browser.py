"""AG_001–AG_007: real run-owned Chromium guarded execution, offline reviewer."""

import pytest

from tests.signal_scout.conftest import lifecycle
from tests.signal_scout.security.test_browser_boundary import fixture_web
from tests.signal_scout.security.test_browser_safeguards import OBSERVE


SETUP = r'''
import importlib,json,struct,time
Browser=importlib.import_module('plugins.signal-scout.security.browser').Browser
g=importlib.import_module('plugins.signal-scout.security.gate')
e=importlib.import_module('plugins.signal-scout.security.evidence')
class Reviewer:
    def __init__(self): self.calls=[];self.callback=None;self.verdict='approve'
    def review(self,**values):
        self.calls.append(values['action_type'])
        assert values['screenshot_mime']=='image/png' and values['screenshot'][:8]==b'\x89PNG\r\n\x1a\n'
        if self.callback: self.callback(values)
        return g.ReviewResult(self.verdict,None,1,0,{})
def reference(snapshot,label):
    lines=[line for line in snapshot.splitlines() if json.dumps(label) in line and '[@s' in line]
    assert len(lines)==1, 'Fixture reference missing or ambiguous'
    return lines[0].rsplit('[',1)[1][:-1]
def wait_ready(browser):
    deadline=time.monotonic()+browser.settings['browser']['command_timeout']
    while not browser.command('eval','window.fixtureReady===true')['result']:
        assert time.monotonic()<deadline,'Controlled fixture did not load'
def open_fixture(browser,path='/gate'):
    browser.navigate('http://public.test'+path);wait_ready(browser)
def dispatch(gate,action,args=None,activity='Inspect controlled research controls'):
    return gate.dispatch({'action':action,'args':{} if args is None else args,'activity':activity})
def successful(gate,action,args=None):
    result=dispatch(gate,action,args)
    assert result['status']=='success',result['reason']
    assert result['reason'] is None and result['execution_started'] is True
    if action in {'click','navigate','type','keypress','back','scroll','switch_tab'}:
        assert set(result['data'])=={'snapshot','target_id'} and isinstance(result['data']['snapshot'],str)
        assert result['data']['target_id']==gate._selected
    return result['data']
'''


def test_AG_008_visible_unfinished_load_returns_incomplete_current_page(fixture_web):
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + r'''
with Browser() as browser:
    adapter=browser.evidence();tab=adapter.tabs()[0]['target_id'];reviewer=Reviewer()
    gate=g.BrowserActionGate(browser,reviewer=reviewer,target_id=tab)
    adapter._cdp.timeout=1
    result=dispatch(gate,'navigate',{'url':'http://public.test/navigation/always/red-proof'})
    assert result['status']=='incomplete',result
    assert result['execution_started'] is True and result['data']['target_id']==tab
    assert 'Navigation attempt 2' in result['data']['snapshot']
    assert reviewer.calls==['navigate','navigate']
    assert browser.ready and not browser.failed
    assert successful(gate,'navigate',{'url':'http://public.test/safe'})['target_id']==tab
''', timeout=120)


def test_AG_008_failed_load_retries_original_url_with_fresh_review(fixture_web):
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + r'''
with Browser() as browser:
    evidence=browser.evidence();tab=evidence.tabs()[0]['target_id'];reviewer=Reviewer()
    gate=g.BrowserActionGate(browser,reviewer=reviewer,target_id=tab)
    connection=evidence._cdp;connection.timeout=1
    native=connection.connection.send;sent=[];screens=[]
    def send(raw):
        sent.append(json.loads(raw));return native(raw)
    connection.connection.send=send
    def review(values):
        screens.append(values['screenshot'])
        assert struct.unpack('>II',values['screenshot'][16:24])==(1280,720)
    reviewer.callback=review
    url='http://public.test/navigation/once/retry-success'
    result=dispatch(gate,'navigate',{'url':url})
    assert result['status']=='success',result
    assert 'Navigation attempt 2' in result['data']['snapshot']
    assert result['execution_started'] is True and result['data']['target_id']==tab
    assert reviewer.calls==['navigate','navigate'] and len(screens)==2 and screens[0]!=screens[1]
    assert [(row['method'],row['params']) for row in sent if row['method']=='Page.navigate']==[
        ('Page.navigate',{'url':url}),('Page.navigate',{'url':url})]
    assert not any(row['method'].startswith('Input.') or row['method'] in {'Page.reload','Page.navigateToHistoryEntry'} for row in sent)
    assert evidence._cdp is connection and browser.ready and not browser.failed
''', timeout=120)


def test_AG_008_native_error_page_remains_observable(fixture_web):
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + r'''
with Browser() as browser:
    evidence=browser.evidence();tab=evidence.tabs()[0]['target_id'];reviewer=Reviewer()
    gate=g.BrowserActionGate(browser,reviewer=reviewer,target_id=tab)
    connection=evidence._cdp;connection.timeout=2
    native=connection.navigate;outcomes=[]
    def navigate(*args):
        outcome=native(*args);outcomes.append(outcome.value);return outcome
    connection.navigate=navigate
    result=dispatch(gate,'navigate',{'url':'http://public.test/navigation-empty404'})
    assert result['status']=='incomplete',(result,outcomes)
    assert result['execution_started'] is True and result['data']['target_id']==tab
    assert 'Reload' in result['data']['snapshot'],(result,outcomes)
    assert outcomes==['load_error','load_error'],outcomes
    assert browser.ready and not browser.failed and evidence._cdp is connection
    successful(gate,'navigate',{'url':'http://public.test/safe'})
''', timeout=120)


def test_AG_008_loaded_404_and_same_cross_origin_redirects_complete_once(fixture_web):
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + r'''
with Browser() as browser:
    evidence=browser.evidence();tab=evidence.tabs()[0]['target_id'];reviewer=Reviewer()
    gate=g.BrowserActionGate(browser,reviewer=reviewer,target_id=tab)
    evidence._cdp.timeout=2
    for url,heading in [
        ('http://public.test/navigation404','Loaded 404 fixture'),
        ('http://public.test/redirect/http://public.test/safe','public fixture survives'),
        ('http://public.test/redirect/http://v6.test/safe','public fixture survives')]:
        before=len(reviewer.calls)
        result=dispatch(gate,'navigate',{'url':url})
        assert result['status']=='success',result
        assert heading in result['data']['snapshot'] and result['data']['target_id']==tab
        assert len(reviewer.calls)==before+1
    assert browser.ready and not browser.failed
''', timeout=120)


@pytest.mark.parametrize('decision', ['deny','stale'])
def test_AG_008_retry_denial_or_staleness_returns_current_ax_without_second_effect(fixture_web, decision):
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + '\ndecision='+repr(decision)+'\n' + r'''
with Browser() as browser:
    evidence=browser.evidence();tab=evidence.tabs()[0]['target_id'];reviewer=Reviewer()
    gate=g.BrowserActionGate(browser,reviewer=reviewer,target_id=tab)
    connection=evidence._cdp;connection.timeout=1
    native=connection.connection.send;sent=[]
    def send(raw):
        sent.append(json.loads(raw));return native(raw)
    connection.connection.send=send
    def changed(values):
        if len(reviewer.calls)==2:
            if decision=='deny': reviewer.verdict='deny'
            else: browser.command('eval',"history.pushState({},'', '/changed-context');true")
    reviewer.callback=changed
    result=dispatch(gate,'navigate',{'url':'http://public.test/navigation/always/retry-'+decision})
    assert result['status']=='incomplete',result
    assert result['execution_started'] is True and result['data']['target_id']==tab
    assert 'Navigation attempt 1' in result['data']['snapshot']
    assert reviewer.calls==['navigate','navigate']
    assert len([row for row in sent if row['method']=='Page.navigate'])==1
    assert browser.ready and not browser.failed and evidence._cdp is connection
    reviewer.verdict='approve';reviewer.callback=None
    successful(gate,'navigate',{'url':'http://public.test/safe'})
''', timeout=120)


def test_AG_001_AG_002_AG_005_approved_actions_and_fresh_compact_feedback(fixture_web):
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + r'''
with Browser() as browser:
    adapter=browser.evidence();tab=adapter.tabs()[0]['target_id'];reviewer=Reviewer()
    gate=g.BrowserActionGate(browser,reviewer=reviewer,target_id=tab)
    captures=[];native=adapter._command
    def command(method,params=None,session=None):
        if method=='Page.captureScreenshot': captures.append(method)
        return native(method,params,session)
    adapter._command=command
    result=successful(gate,'navigate',{'url':'http://public.test/gate'})
    wait_ready(browser)
    snapshot=successful(gate,'snapshot')['snapshot']
    assert 'Guarded research controls' in snapshot and '<html' not in snapshot
    result=successful(gate,'click',{'ref':reference(snapshot,'Controlled click')})
    assert browser.command('eval','window.clicks')['result']==1
    assert 'Clicked 1' in result['snapshot'] and result['snapshot']==adapter.snapshot(tab)
    request={'action':'type','args':{'ref':reference(snapshot,'Gate query'),'text':'exact private type'},'activity':'Fill fixture query'}
    def mutate(values):
        assert values['action']['text']=='exact private type'
        request['args']['text']='caller changed type'
        values['action']['text']='reviewer changed type'
    reviewer.callback=mutate
    result=gate.dispatch(request);assert result['status']=='success'
    assert browser.command('eval',"document.querySelector('#query').value")['result']=='exact private type'
    assert set(result)=={'status','reason','execution_started','data'}
    assert result['reason'] is None and result['execution_started'] is True
    assert result['data']=={'snapshot':adapter.snapshot(tab),'target_id':tab}
    assert 'exact private type' not in repr(gate)
    reviewer.callback=None
    successful(gate,'keypress',{'key':'Enter'})
    assert browser.command('eval','window.submits')['result']==1
    assert browser.command('eval','window.keys.at(-1)')['result']=='Enter'
    successful(gate,'keypress',{'key':'Control+a'})
    successful(gate,'type',{'ref':reference(snapshot,'Gate query'),'text':''})
    assert browser.command('eval',"document.querySelector('#query').value")['result']==''
    assert reviewer.calls==['navigate','click','type','keypress','keypress','type']
    assert len(captures)==len(reviewer.calls),'Main-agent interaction unexpectedly captured screenshot'
    assert successful(gate,'snapshot')['snapshot']==adapter.snapshot(tab)
''', timeout=150)


@pytest.mark.parametrize('change', ['deny','replacement','form','focus','hidden','covered','document'])
def test_AG_002_AG_003_rejection_never_executes_proposal_and_browser_continues(fixture_web, change):
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + '\nchange=' + repr(change) + r'''
with Browser() as browser:
    open_fixture(browser)
    adapter=browser.evidence();tab=adapter.tabs()[0]['target_id'];reviewer=Reviewer()
    gate=g.BrowserActionGate(browser,reviewer=reviewer,target_id=tab)
    snapshot=successful(gate,'snapshot')['snapshot']
    action='keypress' if change=='focus' else 'type' if change=='form' else 'click'
    args={'key':'Enter'} if action=='keypress' else {'ref':reference(snapshot,'Gate query'),'text':'never inserted'} if action=='type' else {'ref':reference(snapshot,'Controlled click')}
    if action=='keypress': browser.command('eval',"document.querySelector('#query').focus();true")
    def changed(_):
        script={
          'replacement':"document.querySelector('#click').replaceWith(document.querySelector('#click').cloneNode(true));true",
          'form':"document.querySelector('#form').action='/purchase';true",
          'focus':"document.querySelector('#other').focus();true",
          'hidden':"document.querySelector('#click').style.visibility='hidden';true",
          'covered':"const cover=document.createElement('div');cover.style='position:fixed;inset:0;z-index:999';document.body.append(cover);true",
          'document':"location.reload();true",'deny':'true'}[change]
        browser.command('eval',script)
        if change=='document': wait_ready(browser)
    reviewer.callback=changed
    if change=='deny': reviewer.verdict='deny'
    result=dispatch(gate,action,args)
    assert result['status']=='rejected' and result['execution_started'] is False and result['data']=={}
    assert len(reviewer.calls)==1 and not browser.failed
    assert browser.command('eval','window.clicks')['result']==0
    assert browser.command('eval','window.submits')['result']==0
    assert browser.command('eval',"document.querySelector('#query').value")['result']=='initial'
    assert successful(gate,'snapshot')['target_id']==tab
''', timeout=150)


def test_AG_002_AG_003_same_and_cross_origin_exact_nodes_focus_and_movement(fixture_web):
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + r'''
with Browser() as browser:
    open_fixture(browser)
    adapter=browser.evidence();tab=adapter.tabs()[0]['target_id'];reviewer=Reviewer()
    gate=g.BrowserActionGate(browser,reviewer=reviewer,target_id=tab)
    snapshot=successful(gate,'snapshot')['snapshot']
    buttons=[line.rsplit('[',1)[1][:-1] for line in snapshot.splitlines() if '- button "Frame button" [@s' in line]
    assert len(buttons)==2
    targets=set()
    for button in buttons:
        binding=adapter._refs[button];targets.add(binding.target)
        owner='same' if binding.target==tab else 'cross'
        def move(_): browser.command('eval',"document.querySelector('#"+owner+"').style.position='relative';document.querySelector('#"+owner+"').style.left='40px';true")
        reviewer.callback=move
        successful(gate,'click',{'ref':button})
        reviewer.callback=None
        frame=adapter._frames(tab)[binding.frame]
        assert adapter._evaluate(frame,"document.querySelector('#child').dataset.clicked")['value']=='true'
        input_ref=next(ref for ref,item in adapter._refs.items() if item.frame==binding.frame and
            item.document==binding.document and ref!=button)
        successful(gate,'type',{'ref':input_ref,'text':'frame-specific value'})
        assert adapter._evaluate(frame,"document.querySelector('#childinput').value")['value']=='frame-specific value'
        successful(gate,'keypress',{'key':'Enter'})
        assert adapter._evaluate(frame,"document.querySelector('#childinput').dataset.keys")['value']=='["Enter"]'
        assert browser.command('eval','document.activeElement.id')['result']==owner
    assert tab in targets and len(targets)==2
    open_fixture(browser,'/evidence-focus')
    snapshot=successful(gate,'snapshot')['snapshot']
    successful(gate,'type',{'ref':reference(snapshot,'Closed first'),'text':'closed original'})
    successful(gate,'keypress',{'key':'Enter'})
    assert browser.command('eval','window.shadowInputs[0].value')['result']=='closed original'
    assert browser.command('eval','window.shadowInputs[1].value')['result']=='same'
    assert browser.command('eval','window.keys')['result']==1
''', timeout=180)


def test_AG_004_AG_005_exempt_observations_selection_scroll_back_close_and_media(fixture_web):
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + r'''
with Browser() as browser:
    open_fixture(browser)
    adapter=browser.evidence();main=adapter.tabs()[0]['target_id'];reviewer=Reviewer()
    browser.settings['browser']['scroll_pixels']=321
    gate=g.BrowserActionGate(browser,reviewer=reviewer,target_id=main)
    snapshot=successful(gate,'snapshot')['snapshot']
    assert successful(gate,'list_tabs')['tabs']==adapter.tabs()
    images=successful(gate,'get_images')['images']
    assert images==[{'src':'http://public.test/media/image.png','alt':'Fixture image','width':1,'height':1}]
    raw=successful(gate,'screenshot')['screenshot']
    assert raw['mime_type']=='image/png' and isinstance(raw['bytes'],bytes)
    assert struct.unpack('>II',raw['bytes'][16:24])==(1280,720)
    successful(gate,'scroll',{'direction':'down'})
    assert browser.command('eval','scrollY')['result']==321
    successful(gate,'scroll',{'direction':'up'})
    assert browser.command('eval','scrollY')['result']==0
    successful(gate,'click',{'ref':reference(snapshot,'Play audio')})
    assert browser.command('eval',"!document.querySelector('#audio').paused && document.querySelector('#audio').readyState>=2")['result']
    successful(gate,'click',{'ref':reference(snapshot,'Download fixture')})
    assert browser.command('eval','location.pathname')['result']=='/gate'
    successful(gate,'click',{'ref':reference(snapshot,'Open popup')})
    assert gate._selected==main
    tabs=successful(gate,'list_tabs')['tabs'];popup=next(item['target_id'] for item in tabs if item['target_id']!=main)
    assert dispatch(gate,'switch_tab',{'target_id':'not-an-existing-tab'})['status']=='rejected'
    assert gate._selected==main
    deadline=time.monotonic()+browser.settings['browser']['command_timeout']
    while True:
        frame=gate._root(adapter._frames(popup))
        if adapter._evaluate(frame,"document.readyState==='complete' && !!document.querySelector('#click')")['value']: break
        assert time.monotonic()<deadline
    size=adapter._evaluate(frame,'({width:innerWidth,height:innerHeight,scale:devicePixelRatio})')['value']
    successful(gate,'switch_tab',{'target_id':popup})
    assert gate._selected==popup and (size['width'],size['height'])!=(1280,720)
    raw=successful(gate,'screenshot')['screenshot']['bytes']
    assert struct.unpack('>II',raw[16:24])==(round(size['width']*size['scale']),round(size['height']*size['scale']))
    assert adapter._evaluate(frame,'({width:innerWidth,height:innerHeight,scale:devicePixelRatio})')['value']==size
    successful(gate,'switch_tab',{'target_id':main})
    successful(gate,'navigate',{'url':'http://public.test/safe'})
    successful(gate,'back')
    assert adapter._evaluate(gate._root(adapter._frames(main)),'location.pathname')['value']=='/gate'
    assert reviewer.calls==['click','click','click','navigate']
    assert successful(gate,'close')=={'closed':True}
    assert dispatch(gate,'snapshot')['status']=='fatal' and browser.failed and not browser.ready
''', timeout=180)


def test_AG_001_AG_004_AG_007_modes_and_privacy_reach_real_gate(fixture_web):
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + r'''
with Browser() as browser:
    open_fixture(browser)
    policy=browser.settings['browser']['action_permissions']
    policy['click']='blocked';policy['type']='allowed_without_review';policy['snapshot']='review_required'
    del policy['navigate']
    adapter=browser.evidence();tab=adapter.tabs()[0]['target_id'];snapshot=adapter.snapshot(tab)
    reviewer=Reviewer();gate=g.BrowserActionGate(browser,reviewer=reviewer,target_id=tab)
    captures=[];native=adapter._command
    def command(method,params=None,session=None):
        if method=='Page.captureScreenshot': captures.append(True)
        return native(method,params,session)
    adapter._command=command
    for action,args in [('click',{'ref':reference(snapshot,'Controlled click')}),
      ('navigate',{'url':'http://public.test/safe'}),('snapshot',{})]:
        result=dispatch(gate,action,args)
        assert result['status']=='rejected' and result['execution_started'] is False
    result=gate.dispatch({'action':'type','args':{'ref':reference(snapshot,'Gate query'),'text':'unlogged-type-poison'}})
    assert result['status']=='success' and not reviewer.calls and not captures
    assert browser.command('eval',"document.querySelector('#query').value")['result']=='unlogged-type-poison'
    assert set(result)=={'status','reason','execution_started','data'}
    assert result['reason'] is None and result['execution_started'] is True
    assert result['data']=={'snapshot':adapter.snapshot(tab),'target_id':tab}
    assert 'unlogged-type-poison' not in repr(gate)
    assert browser.command('eval','window.clicks')['result']==0
    from pathlib import Path
    for path in Path('/var/lib/scout/logs').glob('*.log*'):
        assert b'unlogged-type-poison' not in path.read_bytes()
    assert 'unlogged-type-poison' not in repr(gate._evidence._refs)
''', timeout=150)


@pytest.mark.parametrize('phase', ['capture','dispatch','observation'])
def test_AG_005_AG_006_required_failure_terminal_started_and_owned_cleanup(fixture_web, phase):
    """SR_022 persists original browser failures without changing owned cleanup."""
    stack, _, _, _ = fixture_web
    helper = stack.compose('ps','-q','proxy')
    with stack.browser_run() as run:
        stack.browser_probe(SETUP + '\nphase=' + repr(phase) + r'''
from pathlib import Path
import uuid
import hermes_logging
runtime=importlib.import_module('plugins.signal-scout.security.runtime_logging')
settings=importlib.import_module('plugins.signal-scout.security.settings').load_settings()
runtime.configure_logging('scout',settings)
tag='diagnostic-browser-'+uuid.uuid4().hex
hermes_logging.set_session_context(tag)
with Browser() as browser:
    open_fixture(browser)
    adapter=browser.evidence();tab=adapter.tabs()[0]['target_id'];reviewer=Reviewer()
    gate=g.BrowserActionGate(browser,reviewer=reviewer,target_id=tab)
    snapshot=successful(gate,'snapshot')['snapshot'];native=adapter._command;dispatched=[]
    def broken(method,params=None,session=None):
        if phase=='capture' and method=='Page.captureScreenshot':
            raise RuntimeError('private evidence poison')
        if phase=='dispatch' and method=='Input.dispatchMouseEvent':
            dispatched.append(True)
            return native('Scout.executionDoesNotExist',{},session)
        return native(method,params,session)
    adapter._command=broken
    if phase=='observation':
        def snapshot_failure(_):
            assert browser.command('eval','window.clicks')['result']==1
            raise RuntimeError('private post-action poison')
        adapter.snapshot=snapshot_failure
    result=dispatch(gate,'click',{'ref':reference(snapshot,'Controlled click')})
    assert result['status']=='fatal' and result['execution_started'] is (phase!='capture')
    assert result['data']=={} and 'poison' not in repr(result)
    assert browser.failed and not browser.ready
    assert dispatch(gate,'snapshot')['status']=='fatal'
    assert len(reviewer.calls)==(0 if phase=='capture' else 1)
    assert len(dispatched)==(1 if phase=='dispatch' else 0)
    hermes_logging.flush_log_queue()
    for name in ('agent.log','errors.log'):
        text=(Path('/var/lib/scout/logs')/name).read_text()
        saved=text[text.index(' ['+tag+']'):]
        assert 'action=click phase='+{'capture':'preparation','dispatch':'execution','observation':'observation'}[phase] in saved
        assert 'elapsed_ms=' in saved and 'Traceback (most recent call last):' in saved
        if phase=='dispatch':
            assert 'method=Scout.executionDoesNotExist' in saved
            assert 'protocol_code=-32601' in saved and 'wasn\'t found' in saved
            assert 'response_received=True' in saved and 'wait_state=command_response' in saved
            assert '_ProtocolError: Browser inspection protocol failed.' in saved
        else:
            assert ('private evidence poison' if phase=='capture' else 'private post-action poison') in saved
''', run=run, timeout=150)
    assert run.container is None and run.lock is None
    assert not lifecycle.checked(['docker','ps','-aq','--filter','label='+lifecycle.RUN_LABEL+'='+run.run])
    assert stack.compose('ps','-q','proxy') == helper


@pytest.mark.parametrize('kind', ['attachment','redirect'])
def test_AG_002_AG_005_SR_005_SR_006_SR_010_approved_download_navigation_keeps_browser_healthy(fixture_web, kind):
    stack, _, _, _ = fixture_web
    with stack.browser_run() as run:
        stack.browser_probe(SETUP + OBSERVE + '\nkind=' + repr(kind) + r'''
with Browser() as browser, connect(os.environ['BROWSER_CDP_URL'], proxy=None, open_timeout=2) as observer:
    open_fixture(browser)
    adapter=browser.evidence();tab=adapter.tabs()[0]['target_id'];reviewer=Reviewer()
    gate=g.BrowserActionGate(browser,reviewer=reviewer,target_id=tab)
    initial=successful(gate,'snapshot')['snapshot']
    browser.command('eval',"document.querySelector('#state').textContent='Fresh enforcement fixture';true")
    session=attach(next(page for page in pages() if page['targetId']==tab))
    cdp('Page.enable',session=session);events.clear()
    responses=[];native_navigation=gate._navigation
    def navigation(*args):
        result=native_navigation(*args);responses.append(result);return result
    gate._navigation=navigation
    destination='http://public.test/attachment/gate-fixture.bin'
    if kind=='redirect': destination='http://public.test/redirect/'+destination
    result=dispatch(gate,'navigate',{'url':destination})
    assert result=={'status':'success','reason':None,'execution_started':True,
                   'data':{'snapshot':adapter.snapshot(tab),'target_id':tab}}
    assert 'Fresh enforcement fixture' in result['data']['snapshot'] and result['data']['snapshot']!=initial
    assert len(responses)==1 and responses[0].value=='download_denied'
    assert reviewer.calls==['navigate'] and gate._selected==tab and not gate._terminal
    assert browser.ready and not browser.failed
    began,canceled={},set();deadline=time.monotonic()+10
    while not began or canceled!=set(began):
        message=events.pop(0) if events else json.loads(observer.recv(timeout=max(0.01,deadline-time.monotonic())))
        if message.get('sessionId')!=session: continue
        method,params=message.get('method'),message.get('params',{})
        if method=='Page.downloadWillBegin':
            assert params['suggestedFilename']=='gate-fixture.bin'
            began[params['guid']]=params['url']
        elif method=='Page.downloadProgress':
            assert params['state']!='completed'
            if params['state']=='canceled': canceled.add(params['guid'])
        assert time.monotonic()<deadline,'Approved navigation download did not reach native cancellation'
    assert len(began)==1 and next(iter(began.values()))=='http://public.test/attachment/gate-fixture.bin'
    assert successful(gate,'snapshot')['snapshot']==adapter.snapshot(tab)
    successful(gate,'navigate',{'url':'http://public.test/safe'})
    assert adapter._evaluate(gate._root(adapter._frames(tab)),'location.pathname')['value']=='/safe'
    assert reviewer.calls==['navigate','navigate'] and len(responses)==2
    assert browser.ready and not browser.failed and gate._selected==tab
''', run=run, timeout=150)
        lifecycle.checked(['docker','exec',run.container,'python3','-c',
            "from pathlib import Path; assert not [p for p in Path('/tmp').rglob('*') "
            "if p.name=='gate-fixture.bin' or p.suffix=='.crdownload']"])


@pytest.mark.parametrize('current', ['deny','approve','timeout'])
def test_AG_006_late_review_cannot_execute_or_authorize_current_attempt(fixture_web, current):
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + '\ncurrent=' + repr(current) + r'''
import threading
r=importlib.import_module('plugins.signal-scout.security.reviewer')
entered=[threading.Event(),threading.Event()];release=[threading.Event(),threading.Event()]
finished=[threading.Event(),threading.Event()];attempts=[]
def attempt(settings,policy,evidence,deadline,completed):
    index=len(attempts);attempts.append(index);entered[index].set()
    try:
        assert release[index].wait(10)
        when=time.monotonic()
        if when<deadline: completed.put(('approve' if index==0 else current,None,{},when))
    finally: finished[index].set()
r._attempt=attempt
with Browser() as browser:
    open_fixture(browser)
    adapter=browser.evidence();tab=adapter.tabs()[0]['target_id'];snapshot=adapter.snapshot(tab)
    reviewer=r.SafetyReviewer();reviewer._settings.update(timeout_seconds=1,retries=1)
    gate=g.BrowserActionGate(browser,reviewer=reviewer,target_id=tab)
    results=[]
    owner=threading.Thread(target=lambda:results.append(dispatch(gate,'click',{'ref':reference(snapshot,'Controlled click')})),daemon=True)
    owner.start()
    try:
        assert entered[0].wait(5) and entered[1].wait(5)
        release[0].set();assert finished[0].wait(5)
        assert not results
        # Gate worker waits only in reviewer; main thread's independent native
        # transport proves discarded first approval caused no click.
        assert browser.command('eval','window.clicks')['result']==0
        if current!='timeout': release[1].set()
        owner.join(5);assert not owner.is_alive()
        result=results[0]
        assert result['status']=={'deny':'rejected','approve':'success','timeout':'fatal'}[current]
        assert result['execution_started'] is (current=='approve')
        if current!='timeout':
            assert browser.command('eval','window.clicks')['result']==(1 if current=='approve' else 0)
        else:
            assert browser.failed and not browser.ready
            assert dispatch(gate,'click',{'ref':reference(snapshot,'Controlled click')})['status']=='fatal'
        release[1].set();assert finished[1].wait(5)
        assert attempts==[0,1]
    finally:
        for event in release: event.set()
        owner.join(5)
        assert all(event.wait(5) for event in finished)
''', timeout=150)
