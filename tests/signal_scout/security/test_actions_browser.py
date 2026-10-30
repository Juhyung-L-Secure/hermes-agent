from tests.signal_scout.support.browser import SETUP
import pytest

from tests.signal_scout.support.stack import lifecycle
from tests.signal_scout.support.browser_policy import OBSERVE


@pytest.mark.parametrize('retries', [0,1,4])
def test_AG_008_stalled_image_returns_ready_before_image_completion(fixture_web, retries):
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + '\nretries='+repr(retries)+'\n' + r'''
with Browser() as browser:
    browser.settings['browser']['navigation_retries']=retries
    adapter=browser.session();tab=adapter._tabs()[0]['target_id']
    gate=browser.session(target_id=tab)
    connection=adapter._cdp;connection.timeout=1
    native=connection.connection.send;sent=[];snapshots=[];outcomes=[]
    def send(raw):
        sent.append(json.loads(raw));return native(raw)
    connection.connection.send=send
    native_page=adapter._page
    def page(target):
        value=native_page(target);snapshots.append((target,value.snapshot));return value
    adapter._page=page
    native_navigate=connection.observe
    def navigate(*args,**kwargs):
        value=native_navigate(*args,**kwargs);outcomes.append(value.outcome.value);return value
    connection.observe=navigate
    url='http://public.test/navigation/always/first-observation-'+str(retries)
    result=dispatch(gate,'navigate',{'url':url})
    assert result['status']=='success',result
    assert result['data']['observation']['state']=='ready'
    assert result['data']['observation']['url']==url
    assert browser.command('eval','document.images.length===1 && !document.images[0].complete')['result']
    assert result['execution_started'] is True and result['data']['target_id']==tab
    assert 'Navigation attempt 1' in result['data']['snapshot']
    assert snapshots==[(tab,result['data']['snapshot'])]
    assert outcomes==['ready']
    assert [row['params']['url'] for row in sent if row['method']=='Page.navigate']==[url]
    assert adapter._cdp is connection and browser.ready and not browser.failed
    assert successful(gate,'navigate',{'url':'http://public.test/safe'})['target_id']==tab
''', timeout=120)


@pytest.mark.parametrize('mode,status,last', [('once','success','ready'),('always','incomplete','observation_timeout')])
def test_AG_008_failed_load_retries_original_url_with_current_observation(fixture_web, mode, status, last):
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + '\nmode,status,last='+repr((mode,status,last))+'\n' + r'''
with Browser() as browser:
    browser.settings['browser']['navigation_retries']=4
    evidence=browser.session();tab=evidence._tabs()[0]['target_id']
    gate=browser.session(target_id=tab)
    connection=evidence._cdp;connection.timeout=2;connection.observation_timeout=.5
    native=connection.connection.send;sent=[];screens=[];outcomes=[]
    def send(raw):
        sent.append(json.loads(raw));return native(raw)
    connection.connection.send=send
    native_navigate=connection.observe
    def navigate(*args,**kwargs):
        value=native_navigate(*args,**kwargs);outcomes.append(value.outcome.value);return value
    connection.observe=navigate
    url='http://public.test/navigation-error/'+mode+'/retry-'+last
    result=dispatch(gate,'navigate',{'url':url})
    assert result['status']==status,result
    assert 'Navigation attempt 2' in result['data']['snapshot']
    assert result['execution_started'] is True and result['data']['target_id']==tab
    assert not any(row['method']=='Page.captureScreenshot' for row in sent)
    assert outcomes==['loading_error',last],outcomes
    assert [(row['method'],row['params']) for row in sent if row['method']=='Page.navigate']==[
        ('Page.navigate',{'url':url}),('Page.navigate',{'url':url})]
    assert not any(row['method'].startswith('Input.') or row['method'] in {'Page.reload','Page.navigateToHistoryEntry'} for row in sent)
    assert evidence._cdp is connection and browser.ready and not browser.failed
''', timeout=120)


def test_AG_008_native_error_page_remains_observable(fixture_web):
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + r'''
with Browser() as browser:
    evidence=browser.session();tab=evidence._tabs()[0]['target_id']
    gate=browser.session(target_id=tab)
    connection=evidence._cdp;connection.timeout=2
    native=connection.observe;outcomes=[]
    def navigate(*args,**kwargs):
        outcome=native(*args,**kwargs);outcomes.append(outcome.outcome.value);return outcome
    connection.observe=navigate
    result=dispatch(gate,'navigate',{'url':'http://public.test/navigation-empty404'})
    assert result['status']=='incomplete',(result,outcomes)
    assert result['execution_started'] is True and result['data']['target_id']==tab
    assert 'Reload' in result['data']['snapshot'],(result,outcomes)
    assert outcomes==['loading_error','loading_error'],outcomes
    assert browser.ready and not browser.failed and evidence._cdp is connection
    successful(gate,'navigate',{'url':'http://public.test/safe'})
''', timeout=120)


def test_AG_008_loaded_404_and_same_cross_origin_redirects_complete_once(fixture_web):
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + r'''
with Browser() as browser:
    evidence=browser.session();tab=evidence._tabs()[0]['target_id']
    gate=browser.session(target_id=tab)
    evidence._cdp.timeout=2
    for url,heading in [
        ('http://public.test/navigation404','Loaded 404 fixture'),
        ('http://public.test/redirect/http://public.test/safe','public fixture survives'),
        ('http://public.test/redirect/http://v6.test/safe','public fixture survives')]:
        result=dispatch(gate,'navigate',{'url':url})
        assert result['status']=='success',result
        assert heading in result['data']['snapshot'] and result['data']['target_id']==tab
    assert browser.ready and not browser.failed
''', timeout=120)


def test_AG_001_AG_002_AG_005_allowed_actions_and_fresh_compact_feedback(fixture_web):
    """AG_009"""
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + r'''
with Browser() as browser:
    adapter=browser.session();tab=adapter._tabs()[0]['target_id']
    gate=browser.session(target_id=tab)
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
    assert 'Clicked 1' in result['snapshot'] and result['snapshot']==adapter._inspect_snapshot(tab)
    request={'action':'type','args':{'ref':reference(snapshot,'Gate query'),'text':'exact private type'},'activity':'Fill fixture query'}
    result=gate.dispatch(request);assert result['status']=='success'
    assert browser.command('eval',"document.querySelector('#query').value")['result']=='exact private type'
    assert set(result)=={'status','reason','execution_started','data'}
    assert result['reason'] is None and result['execution_started'] is True
    assert result['data']=={'snapshot':adapter._inspect_snapshot(tab),'target_id':tab}
    assert 'exact private type' not in repr(gate)
    successful(gate,'keypress',{'key':'Enter'})
    assert browser.command('eval','window.submits')['result']==1
    assert browser.command('eval','window.keys.at(-1)')['result']=='Enter'
    successful(gate,'keypress',{'key':'Control+a'})
    successful(gate,'type',{'ref':reference(snapshot,'Gate query'),'text':''})
    assert browser.command('eval',"document.querySelector('#query').value")['result']==''
    assert captures==[], 'Ordinary browser action captured an implicit screenshot'
    png=successful(gate,'screenshot')['screenshot']['bytes']
    assert png[:8]==b'\x89PNG\r\n\x1a\n' and struct.unpack('>II',png[16:24])==(1280,720)
    assert captures==['Page.captureScreenshot']
    assert successful(gate,'snapshot')['snapshot']==adapter._inspect_snapshot(tab)
''', timeout=150)


@pytest.mark.parametrize('change', ['replacement','readonly','hidden','covered','document'])
def test_AG_002_AG_003_rejection_never_executes_proposal_and_browser_continues(fixture_web, change):
    """AE_001 AE_002 AE_004 AE_005 AE_006"""
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + '\nchange=' + repr(change) + r'''
with Browser() as browser:
    open_fixture(browser)
    adapter=browser.session();tab=adapter._tabs()[0]['target_id']
    gate=browser.session(target_id=tab)
    snapshot=successful(gate,'snapshot')['snapshot']
    action='type' if change=='readonly' else 'click'
    args={'key':'Enter'} if action=='keypress' else {'ref':reference(snapshot,'Gate query'),'text':'never inserted'} if action=='type' else {'ref':reference(snapshot,'Controlled click')}
    script={
      'replacement':"document.querySelector('#click').replaceWith(document.querySelector('#click').cloneNode(true));true",
      'readonly':"document.querySelector('#query').readOnly=true;true",
      'hidden':"document.querySelector('#click').style.visibility='hidden';true",
      'covered':"const cover=document.createElement('div');cover.style='position:fixed;inset:0;z-index:999';document.body.append(cover);true",
      'document':"location.reload();true"}[change]
    browser.command('eval',script)
    if change=='document': wait_ready(browser)
    result=dispatch(gate,action,args)
    assert result['status']=='rejected' and result['execution_started'] is False and result['data']=={}
    assert not browser.failed
    assert browser.command('eval','window.clicks')['result']==0
    assert browser.command('eval','window.submits')['result']==0
    assert browser.command('eval',"document.querySelector('#query').value")['result']=='initial'
    assert successful(gate,'snapshot')['target_id']==tab
''', timeout=150)


def test_AG_002_AG_003_same_and_cross_origin_exact_nodes_focus_and_movement(fixture_web):
    """AE_001 AE_002 AE_004 AE_005"""
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + r'''
with Browser() as browser:
    open_fixture(browser)
    adapter=browser.session();tab=adapter._tabs()[0]['target_id']
    gate=browser.session(target_id=tab)
    snapshot=successful(gate,'snapshot')['snapshot']
    buttons=[line.rsplit('[',1)[1][:-1] for line in snapshot.splitlines() if '- button "Frame button" [@s' in line]
    assert len(buttons)==2
    targets=set()
    for button in buttons:
        binding=adapter._refs[button];targets.add(binding.target)
        owner='same' if binding.target==tab else 'cross'
        browser.command('eval',"document.querySelector('#"+owner+"').style.position='relative';document.querySelector('#"+owner+"').style.left='40px';true")
        successful(gate,'click',{'ref':button})
        frame=adapter._frames(tab).frames[binding.frame]
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


def test_AG_004_AG_005_observations_selection_scroll_back_close_and_media(fixture_web):
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + r'''
with Browser() as browser:
    open_fixture(browser)
    browser.settings['browser']['scroll_pixels']=321
    adapter=browser.session();main=adapter._tabs()[0]['target_id']
    gate=browser.session(target_id=main)
    snapshot=successful(gate,'snapshot')['snapshot']
    assert successful(gate,'list_tabs')['tabs']==adapter._tabs()
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
    connection=adapter._cdp;native_event=connection._event;native_send=connection.connection.send
    events=[];effects=[]
    def event(packet):
        method=packet.get('method');params=packet.get('params',{})
        if method in {'Page.frameRequestedNavigation','Page.frameStartedNavigating','Page.frameStartedLoading',
          'Page.frameStoppedLoading','Page.frameNavigated','Page.lifecycleEvent',
          'Page.downloadWillBegin','Page.downloadProgress','Browser.downloadWillBegin','Browser.downloadProgress'}:
            events.append({'method':method,'session':packet.get('sessionId'),
                **{key:params[key] for key in ('frameId','loaderId','name','type','state') if key in params}})
        return native_event(packet)
    def send(raw):
        command=json.loads(raw)
        if command['method']=='Input.dispatchMouseEvent': effects.append(command['params']['type'])
        return native_send(raw)
    connection._event=event;connection.connection.send=send
    try:
        downloaded=dispatch(gate,'click',{'ref':reference(snapshot,'Download fixture')})
    finally:
        connection._event=native_event;connection.connection.send=native_send
    assert downloaded['status']=='incomplete' and downloaded['execution_started'] is True,downloaded
    state=downloaded['data']['observation']
    assert state['state']=='observation_timeout' and state['commands_acknowledged'] is True
    assert state['action_completed'] is False and state['document_relation']=='same'
    assert state['url']=='http://public.test/gate' and 'Guarded research controls' in downloaded['data']['snapshot']
    assert downloaded['data']['target_id']==main and browser.ready and not browser.failed
    assert effects==['mousePressed','mouseReleased']
    print(json.dumps({'download_click_observation':state,'download_events':events}))
    assert browser.command('eval','location.pathname')['result']=='/gate'
    successful(gate,'click',{'ref':reference(snapshot,'Open popup')})
    assert gate._selected==main
    tabs=successful(gate,'list_tabs')['tabs'];popup=next(item['target_id'] for item in tabs if item['target_id']!=main)
    assert dispatch(gate,'switch_tab',{'target_id':'not-an-existing-tab'})['status']=='rejected'
    assert gate._selected==main
    deadline=time.monotonic()+browser.settings['browser']['command_timeout']
    while True:
        frame=next(frame for frame in adapter._frames(popup).frames.values() if frame.parent is None)
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
    assert adapter._evaluate(next(frame for frame in adapter._frames(main).frames.values() if frame.parent is None),'location.pathname')['value']=='/gate'
    assert successful(gate,'close')=={'closed':True}
    assert dispatch(gate,'snapshot')['status']=='fatal' and browser.failed and not browser.ready
''', timeout=180)


def test_AG_001_AG_004_AG_007_modes_and_privacy_reach_real_gate(fixture_web):
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + r'''
with Browser() as browser:
    open_fixture(browser)
    policy=browser.settings['browser']['action_permissions']
    policy['click']='blocked';policy['type']='allowed';policy['snapshot']='blocked'
    del policy['navigate']
    adapter=browser.session();tab=adapter._tabs()[0]['target_id'];snapshot=adapter._inspect_snapshot(tab)
    gate=browser.session(target_id=tab)
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
    assert result['status']=='success' and not captures
    assert browser.command('eval',"document.querySelector('#query').value")['result']=='unlogged-type-poison'
    assert set(result)=={'status','reason','execution_started','data'}
    assert result['reason'] is None and result['execution_started'] is True
    assert result['data']=={'snapshot':adapter._inspect_snapshot(tab),'target_id':tab}
    assert 'unlogged-type-poison' not in repr(gate)
    assert browser.command('eval','window.clicks')['result']==0
    from pathlib import Path
    for path in Path('/var/lib/scout/logs').glob('*.log*'):
        assert b'unlogged-type-poison' not in path.read_bytes()
    assert 'unlogged-type-poison' not in repr(gate._refs)
''', timeout=150)


@pytest.mark.parametrize('phase', ['inspection','dispatch','observation'])
def test_AG_005_AG_006_required_failure_terminal_started_and_owned_cleanup(fixture_web, phase):
    """AE_006 SR_022 persists original browser failures without changing owned cleanup."""
    stack, _, _, _ = fixture_web
    with stack.browser_run() as run:
        helper = stack.compose('ps','-q','proxy')
        stack.browser_probe(SETUP + '\nphase=' + repr(phase) + r'''
from pathlib import Path
import uuid
import hermes_logging
runtime=importlib.import_module('plugins.signal-scout.scout_runtime.runtime_logging')
settings=importlib.import_module('plugins.signal-scout.scout_runtime.settings').load_settings()
runtime.configure_logging('scout',settings)
tag='diagnostic-browser-'+uuid.uuid4().hex
hermes_logging.set_session_context(tag)
with Browser() as browser:
    open_fixture(browser)
    adapter=browser.session();tab=adapter._tabs()[0]['target_id']
    gate=browser.session(target_id=tab)
    snapshot=successful(gate,'snapshot')['snapshot'];native=adapter._command;dispatched=[]
    def broken(method,params=None,session=None):
        if phase=='inspection' and method=='DOM.getContentQuads':
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
        adapter._page=snapshot_failure
    result=dispatch(gate,'click',{'ref':reference(snapshot,'Controlled click')})
    assert result['status']=='fatal' and result['execution_started'] is (phase!='inspection')
    assert result['data']=={} and 'poison' not in repr(result)
    assert browser.failed and not browser.ready
    assert dispatch(gate,'snapshot')['status']=='fatal'
    assert len(dispatched)==(1 if phase=='dispatch' else 0)
    hermes_logging.flush_log_queue()
    for name in ('agent.log','errors.log'):
        text=(Path('/var/lib/scout/logs')/name).read_text()
        saved=text[text.index(' ['+tag+']'):]
        assert 'action=click phase='+{'inspection':'preparation','dispatch':'execution','observation':'observation'}[phase] in saved
        assert 'elapsed_ms=' in saved and 'Traceback (most recent call last):' in saved
        if phase=='dispatch':
            assert 'method=Scout.executionDoesNotExist' in saved
            assert 'protocol_code=-32601' in saved and 'wasn\'t found' in saved
            assert 'response_received=True' in saved and 'wait_state=command_response' in saved
            assert '_ProtocolError: Browser inspection protocol failed.' in saved
        else:
            assert ('private evidence poison' if phase=='inspection' else 'private post-action poison') in saved
''', run=run, timeout=150)
    assert run.container is None and run.lock is None
    assert not lifecycle.checked(['docker','ps','-aq','--filter','label='+lifecycle.RUN_LABEL+'='+run.run])
    assert stack.compose('ps','-q','proxy') == helper


@pytest.mark.parametrize('kind', ['attachment','redirect'])
def test_AG_002_AG_005_SR_005_SR_006_SR_010_native_download_navigation_keeps_browser_healthy(fixture_web, kind):
    stack, _, _, _ = fixture_web
    with stack.browser_run() as run:
        stack.browser_probe(SETUP + OBSERVE + '\nkind=' + repr(kind) + r'''
with Browser() as browser, connect(os.environ['BROWSER_CDP_URL'], proxy=None, open_timeout=2) as observer:
    open_fixture(browser)
    adapter=browser.session();tab=adapter._tabs()[0]['target_id']
    gate=browser.session(target_id=tab)
    initial=successful(gate,'snapshot')['snapshot']
    browser.command('eval',"document.querySelector('#state').textContent='Fresh enforcement fixture';true")
    session=attach(next(page for page in pages() if page['targetId']==tab))
    cdp('Page.enable',session=session);events.clear()
    responses=[];native_navigation=gate._observe_effect
    def navigation(*args, **kwargs):
        result=native_navigation(*args, **kwargs);responses.append(result);return result
    gate._observe_effect=navigation
    destination='http://public.test/attachment/gate-fixture.bin'
    if kind=='redirect': destination='http://public.test/redirect/'+destination
    result=dispatch(gate,'navigate',{'url':destination})
    assert result['status']=='success' and result['reason'] is None and result['execution_started'] is True
    assert result['data']['snapshot']==adapter._inspect_snapshot(tab) and result['data']['target_id']==tab
    assert result['data']['observation']['state']=='download_denied'
    assert 'Fresh enforcement fixture' in result['data']['snapshot'] and result['data']['snapshot']!=initial
    assert len(responses)==1 and responses[0].outcome.value=='download_denied'
    assert gate._selected==tab and not gate._terminal
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
    assert successful(gate,'snapshot')['snapshot']==adapter._inspect_snapshot(tab)
    successful(gate,'navigate',{'url':'http://public.test/safe'})
    assert adapter._evaluate(next(frame for frame in adapter._frames(tab).frames.values() if frame.parent is None),'location.pathname')['value']=='/safe'
    assert len(responses)==2
    assert browser.ready and not browser.failed and gate._selected==tab
''', run=run, timeout=150)
        lifecycle.checked(['docker','exec',run.container,'python3','-c',
            "from pathlib import Path; assert not [p for p in Path('/tmp').rglob('*') "
            "if p.name=='gate-fixture.bin' or p.suffix=='.crdownload']"])


def test_AG_008_parser_stall_returns_partial_current_ax_then_healthy_next_action(fixture_web):
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + r'''
with Browser() as browser:
    adapter=browser.session();tab=adapter._tabs()[0]['target_id']
    gate=browser.session(target_id=tab)
    connection=adapter._cdp;connection.observation_timeout=.4
    sent=[];native=connection.connection.send
    def send(raw):
        sent.append(json.loads(raw));return native(raw)
    connection.connection.send=send
    url='http://public.test/parser-stall/partial-observation'
    result=dispatch(gate,'navigate',{'url':url})
    assert result['status']=='incomplete' and result['execution_started'] is True,result
    assert result['reason']=='Browser document observation deadline reached after 0.4 seconds.'
    assert 'Visible partial document' in result['data']['snapshot'] and 'Parser released' not in result['data']['snapshot']
    state=result['data']['observation']
    assert state['url']==url and state['ready_state']=='loading' and state['document_relation']=='changed'
    assert state['state']=='observation_timeout' and result['data']['requested_url']==url
    assert browser.command('eval',"fetch('/observation-release/partial-observation');true")['result']
    wait_ready(browser)
    assert 'Parser released' in successful(gate,'snapshot')['snapshot']
    assert successful(gate,'navigate',{'url':'http://public.test/safe'})['target_id']==tab
    assert [r['params']['url'] for r in sent if r['method']=='Page.navigate']==[url,'http://public.test/safe']
    assert adapter._cdp is connection
''',timeout=120)


def test_AG_008_dropdown_link_enter_history_and_pending_old_document(fixture_web):
    stack, _, _, _ = fixture_web
    proof=stack.browser_probe(SETUP + r'''
with Browser() as browser:
    adapter=browser.session();tab=adapter._tabs()[0]['target_id']
    gate=browser.session(target_id=tab)
    connection=adapter._cdp;connection.observation_timeout=.6
    successful(gate,'navigate',{'url':'http://public.test/observation-controls'})
    snapshot=successful(gate,'snapshot')['snapshot']
    start=time.monotonic()
    current=successful(gate,'click',{'ref':reference(snapshot,'Show choices')})
    assert current['observation']['state']=='current' and 'Visible dropdown choices' in current['snapshot']
    assert current['observation']['elapsed_seconds'] < .6
    linked=successful(gate,'click',{'ref':reference(snapshot,'Follow observed link')})
    assert linked['observation']['state']=='ready' and linked['observation']['url'].endswith('/navigation/always/link-observation')
    assert browser.command('eval','!document.images[0].complete')['result']
    restored=successful(gate,'back')
    assert 'Observation controls' in restored['snapshot'] and restored['observation']['url'].endswith('/observation-controls')
    snapshot=restored['snapshot']
    same=successful(gate,'click',{'ref':reference(snapshot,'Same document section')})
    assert same['observation']['state']=='same_document' and same['observation']['url'].endswith('#section')
    same_back=successful(gate,'back')
    assert same_back['observation']['state']=='same_document' and same_back['observation']['url'].endswith('/observation-controls')
    successful(gate,'type',{'ref':reference(same_back['snapshot'],'Navigation query'),'text':'read'})
    entered=successful(gate,'keypress',{'key':'Enter'})
    assert entered['observation']['state']=='ready' and '/navigation/always/enter-observation?q=read' in entered['observation']['url']
    assert 'Navigation attempt 1' in entered['snapshot']
    returned=successful(gate,'back')
    old_url=returned['observation']['url']
    pending=dispatch(gate,'click',{'ref':reference(returned['snapshot'],'Hold destination')})
    assert pending['status']=='incomplete' and pending['execution_started'] is True,pending
    state=pending['data']['observation']
    assert state['state']=='observation_timeout' and state['commands_acknowledged'] is True
    assert state['action_completed'] is False
    if state['url']==old_url:
        assert state['document_relation']=='same' and 'Observation controls' in pending['data']['snapshot']
        assert 'Released destination' not in pending['data']['snapshot']
    else:
        assert state['url']=='http://public.test/navigation-held/pending-old',pending
        assert state['document_relation']=='changed' and 'Released destination' in pending['data']['snapshot']
        assert 'Observation controls' not in pending['data']['snapshot']
    print(json.dumps({'pending_destination':state,'real_history_state':restored['observation']['state']}))
    browser.command('eval',"fetch('/observation-release/pending-old');true")
    assert browser.ready and adapter._cdp is connection
''',timeout=180)
    print(proof)
