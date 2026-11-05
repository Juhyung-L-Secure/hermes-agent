import pytest

from tests.signal_scout.browser_tools.test_native_integration import SETUP
from tests.signal_scout.support.model_fixture import FAKE_MAIN


def test_AG_005_AG_006_acknowledged_input_then_stale_inspection_is_nonfatal(fixture_web):
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + FAKE_MAIN + r'''
with Browser(config) as browser:
    tab=open_fixture(browser);gate=browser.session(target_id=tab)
    ref=reference(gate._inspect_snapshot(tab),'Query')
    original=g._CDP.command;inserted=[];armed=[True]
    def command(self,method,params=None,session=None):
        if method=='Input.insertText':
            inserted.append(params['text'])
        if self.authority is not None and inserted and method=='Runtime.evaluate' and armed[0]:
            armed[0]=False
            return original(self,method,{**params,'contextId':-1},session)
        return original(self,method,params,session)
    def plan(index,kwargs):
        if index==1:
            return response([tool({'action':'type','args':{'ref':ref,'text':'one acknowledged value'}})],content='')
        result=model_result([m for m in kwargs['messages'] if m.get('role')=='tool'][-1])
        if index==2:
            assert result['status']=='incomplete',result
            failure=result['data']['failure']
            assert failure['kind']=='context_changed' and failure['phase']=='observation',result
            assert failure['action_acknowledged'] is True
            assert result['data']['capture']['state']=='fresh'
            assert 'one acknowledged value' in result['data']['snapshot']
            return response([tool({'action':'snapshot','args':{}})],content='')
        assert index==3 and result['status']=='success',result
        assert inserted==['one acknowledged value']
        assert browser.ready and not browser.failed
        return response()
    with patch.object(g._CDP,'command',command),fake_main(plan) as (agent,requests):
        agent._api_max_retries=1
        with adapter.bind_browser(gate,action_reference=action_reference,on_fatal=partial(agent.interrupt,hard_cancel=True)):
            outcome=agent.run_conversation('Continue after acknowledged input loses inspection context.')
            assert outcome.get('completed') and not outcome.get('interrupted'),outcome
            stable_requests(requests)
''', timeout=150)


def test_AG_004_AG_006_selected_crashed_renderer_reload_precedes_inspection(fixture_web):
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + FAKE_MAIN + r'''
with Browser(config) as browser:
    tab=open_fixture(browser);gate=browser.session(target_id=tab)
    prior=gate._frames(tab).frames
    root_id,root_frame=next((key,frame) for key,frame in prior.items() if frame.parent is None)
    previous=root_frame.document
    target=gate._session(tab);gate._cdp.timeout=2
    try:
        gate._command('Page.crash',session=target)
    except Exception:
        pass
    gate._disconnect()
    original=g._CDP.command;commands=[];reloads=[]
    def command(self,method,params=None,session=None):
        if self.authority is not None:
            commands.append(method)
            if method=='Page.reload': reloads.append(session)
        return original(self,method,params,session)
    def plan(index,kwargs):
        if index==1:
            return response([tool({'action':'reload','args':{}})],content='')
        result=model_result([m for m in kwargs['messages'] if m.get('role')=='tool'][-1])
        assert reloads, (result,commands)
        before=commands[:commands.index('Page.reload')]
        assert not {'Page.getFrameTree','Page.createIsolatedWorld','Runtime.enable','Accessibility.getFullAXTree'} & set(before),before
        assert gate._selected==tab and browser.ready and not browser.failed
        assert result['status'] in {'success','incomplete'},result
        if index==2:
            return response([tool({'action':'snapshot','args':{}})],content='')
        assert index==3 and result['status']=='success',result
        assert 'Below fold' in result['data']['snapshot']
        fresh_root_documents={binding.document for binding in gate._refs.values() if binding.frame==root_id}
        assert fresh_root_documents and previous not in fresh_root_documents
        assert len(reloads)==1
        return response()
    with patch.object(g._CDP,'command',command),fake_main(plan) as (agent,requests):
        agent._api_max_retries=1
        with adapter.bind_browser(gate,action_reference=action_reference,on_fatal=partial(agent.interrupt,hard_cancel=True)):
            outcome=agent.run_conversation('Reload selected crashed renderer, then inspect fresh document.')
            assert outcome.get('completed') and not outcome.get('interrupted'),outcome
            stable_requests(requests)
''',timeout=150)


@pytest.mark.parametrize('stage',['discovery','handshake','capture'])
def test_AG_006_AG_010_expired_repair_attempt_cannot_touch_accepted_connection(fixture_web, stage):
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + FAKE_MAIN + '\nstage='+repr(stage)+'\n'+r'''
lifetime=importlib.import_module('plugins.signal-scout.scout_runtime.browser.lifetime')
with Browser(config) as browser:
    tab=open_fixture(browser);gate=browser.session(target_id=tab)
    old_ref=reference(gate._inspect_snapshot(tab),'Below fold')
    config['scout_recovery'].update(attempt_timeout=2,reconnect_attempts=2)
    config['browser']['request_timeout']=20
    parked=threading.Event();release=threading.Event();old=[];armed=[True];lost=[True]
    native_command=g._CDP.command;native_init=g._CDP.__init__
    native_verify=lifetime._RequestBrowser.verify_control;native_snapshot=g.BrowserSession._inspect_snapshot
    def park(authority):
        if authority is not None and authority.parent is not None and armed[0]:
            armed[0]=False;old.append(authority);parked.set()
            assert release.wait(20)
    def command(self,method,params=None,session=None):
        if self.authority is not None and self.authority.parent is None and method=='Input.dispatchMouseEvent' and lost[0]:
            lost[0]=False;self.abort()
        return native_command(self,method,params,session)
    def verify(self):
        if stage=='discovery': park(self.authority)
        return native_verify(self)
    def init(self,*args,**kwargs):
        authority=kwargs.get('authority',args[4] if len(args)>4 else None)
        if stage=='handshake': park(authority)
        return native_init(self,*args,**kwargs)
    def snapshot(self,tab):
        if stage=='capture': park(self._authority)
        return native_snapshot(self,tab)
    def plan(index,kwargs):
        if index==1:
            return response([tool({'action':'click','args':{'ref':old_ref}})],content='')
        result=model_result([m for m in kwargs['messages'] if m.get('role')=='tool'][-1])
        if index==2:
            assert parked.is_set() and result['status']=='incomplete',result
            assert result['data']['recovery']['kind']=='reconnected',result
            assert result['data']['recovery']['reconnect_attempts']==(1 if stage=='capture' else 2),result
            assert result['data']['capture']['state']==('unavailable' if stage=='capture' else 'fresh'),result
            return response([tool({'action':'snapshot','args':{}})],content='')
        if index==3:
            assert result['status']=='success',result
            fresh=reference(result['data']['snapshot'],'Below fold')
            refs=dict(gate._refs);selected=gate._selected
            release.set();assert old[0].done.wait(10)
            assert gate._refs==refs and gate._selected==selected==tab
            assert old[0].revoked.is_set() and browser.ready and not browser.failed
            return response([tool({'action':'click','args':{'ref':fresh}})],content='')
        assert index==4 and result['status']=='success',result
        assert browser.command('eval','window.counts.click')['result']==1
        return response()
    try:
        with patch.object(g._CDP,'command',command),patch.object(g._CDP,'__init__',init), \
             patch.object(lifetime._RequestBrowser,'verify_control',verify), \
             patch.object(g.BrowserSession,'_inspect_snapshot',snapshot),fake_main(plan) as (agent,requests):
            agent._api_max_retries=1
            with adapter.bind_browser(gate,action_reference=action_reference,on_fatal=partial(agent.interrupt,hard_cancel=True)):
                began=time.monotonic()
                outcome=agent.run_conversation('Accept fresh owned repair while expired attempt stays isolated.')
                assert time.monotonic()-began<18
                assert outcome.get('completed') and not outcome.get('interrupted'),outcome
                stable_requests(requests)
    finally:
        release.set()
''',timeout=150)
