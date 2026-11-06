from tests.signal_scout.browser_tools.test_native_integration import SETUP
from tests.signal_scout.support.model_fixture import FAKE_MAIN
from repair_server import RecoveryServer


def test_BR_003_BR_004_AE_006_AG_005_MI_035_unknown_reaches_native_report_and_denies_ignored_input(fixture_web):
    stack, _, _, _ = fixture_web
    with stack.browser_run() as run:
        stack.browser_probe(SETUP + FAKE_MAIN + r'''
mission=importlib.import_module('plugins.signal-scout.scout_runtime.mission')
plugin=get_plugin_manager()._plugins['signal-scout'].module
config['scout_recovery']['strategy']='restart'
with Browser(config) as browser:
    tab=open_fixture(browser);gate=browser.session(target_id=tab)
    ref=reference(gate._inspect_snapshot(tab),'Below fold')
    native_command=g.BrowserSession._command
    def command(self,method,params=None,session=None,**kwargs):
        if method=='DOM.getContentQuads':
            return native_command(self,'Scout.previouslyUnseenInspection',{},session,**kwargs)
        return native_command(self,method,params,session,**kwargs)
    state=mission.MissionControl(mission.MissionRequest('Inspect fixture.'),config['scout_mission'])
    def plan(index,kwargs):
        if index==1:
            return response([tool({'action':'click','args':{'ref':ref}},call_id='unseen')],content='')
        feedback=model_result([m for m in kwargs['messages'] if m.get('role')=='tool'][-1])
        assert browser.command('eval','window.counts.click')['result']==0
        if index==2:
            assert feedback['status']=='incomplete' and feedback['data']['failure']['kind']=='unknown',feedback
            assert feedback['data']['triage']['browser_use']=='unavailable'
            assert feedback['data']['triage']['mission']=='report_only'
            assert any(c['method']=='DOM.scrollIntoViewIfNeeded' and c['acknowledged'] for c in feedback['data']['operation']['commands'])
            return response([tool({'action':'click','args':{'ref':ref}},call_id='ignored-restriction')],content='')
        assert index==3 and feedback['data']['triage']['allowed_operations']==[],feedback
        return response(content='Fixture remains unclicked. Findings incomplete.')
    with patch.object(g.BrowserSession,'_command',command),fake_main(plan,iterations=6) as (agent,requests):
        with adapter.bind_browser(gate,action_reference=action_reference,on_fatal=partial(agent.interrupt,hard_cancel=True),control=state), \
             plugin.model_requests.bind_model_requests(agent,max_attempts=2,control=state):
            native=agent.run_conversation('Inspect fixture and report uncertainty. '+action_reference)
            result=state.finish(native)
            assert mission.MissionOutcome.from_native(result)==mission.MissionOutcome.FAILURE
            assert 'Fixture remains unclicked.' in result['final_response']
            assert result['scout_incomplete'] and native.get('completed') and not native.get('interrupted')
            assert browser.command('eval','window.counts.click')['result']==0
        assert len(requests)==3
        stable_requests(requests)
        assert requests[2]['messages'][:len(requests[1]['messages'])]==requests[1]['messages']
print('unseen failure, persistent denial, native report, stable prefix verified')
''', run=run, timeout=180)
    assert run.container is None


def test_BR_003_BR_004_AE_006_AG_006_AG_010_SR_005_partial_input_requires_explicit_owned_restart(fixture_web):
    stack, _, _, _ = fixture_web
    with stack.browser_run() as run, RecoveryServer(run) as server:
        initial = run.container
        stack.browser_probe(SETUP + FAKE_MAIN + r'''
with Browser(config) as browser:
    tab=open_fixture(browser);gate=browser.session(target_id=tab)
    ref=reference(gate._inspect_snapshot(tab),'Below fold')
    native_command=g.BrowserSession._command
    armed=[True];inputs=[]
    def command(self,method,params=None,session=None,**kwargs):
        if method=='Input.dispatchMouseEvent':
            inputs.append(params['type'])
            if params['type']=='mouseReleased' and armed[0]:
                armed[0]=False
                raise g.ControlUnavailable('Controlled loss before release')
        return native_command(self,method,params,session,**kwargs)
    def plan(index,kwargs):
        if index==1:
            return response([tool({'action':'click','args':{'ref':ref}},call_id='partial')],content='')
        result=model_result([m for m in kwargs['messages'] if m.get('role')=='tool'][-1])
        if index==2:
            assert result['status']=='incomplete' and result['data']['operation']['unresolved_input'],result
            assert result['data']['triage']['browser_use']=='unavailable'
            assert browser.generation==1
            return response([tool({'action':'click','args':{'ref':ref}},call_id='ignored')],content='')
        if index==3:
            assert result['status']=='rejected' and inputs==['mousePressed','mouseReleased'],result
            assert browser.generation==1
            return response([tool({'action':'restart','args':{}},call_id='explicit-replacement')],content='')
        if index==4:
            assert result['data']['recovery']['kind']=='restarted' and browser.generation==2,result
            assert ref not in gate._refs and gate._selected!=tab
            assert result['data']['triage']['browser_use']=='ready'
            return response([tool({'action':'navigate','args':{'url':'http://public.test/evidence'}},call_id='fresh-page')],content='')
        if index==5:
            assert result['status']=='success',result
            return response([tool({'action':'click','args':{'ref':reference(result['data']['snapshot'],'Below fold')}},call_id='fresh-input')],content='')
        assert index==6 and result['status']=='success',result
        assert inputs==['mousePressed','mouseReleased','mousePressed','mouseReleased']
        observer=g._CDP(browser.endpoint,2,1,.25)
        try:
            selected=observer.command('Target.attachToTarget',{'targetId':gate._selected,'flatten':True})['sessionId']
            assert observer.command('Runtime.evaluate',{'expression':'window.counts.click','returnByValue':True},selected)['result']['value']==1
        finally:
            observer.close()
        return response()
    with patch.object(g.BrowserSession,'_command',command),fake_main(plan,iterations=8) as (agent,requests):
        with adapter.bind_browser(gate,action_reference=action_reference,on_fatal=partial(agent.interrupt,hard_cancel=True)):
            outcome=agent.run_conversation('Use only allowed browser operations. '+action_reference)
            assert outcome.get('completed') and not outcome.get('interrupted'),outcome
            assert gate._requests.replacements_remaining==config['scout_recovery']['max_replacements']-1
            stable_requests(requests)
print('partial input fenced until explicit replacement; fresh click once verified')
''', *server.scout_options(), run=run, timeout=240)
        assert run.generation == 2 and run.container != initial
    assert run.container is None


def test_BR_004_MI_032_MI_035_native_middleware_cap_never_dispatches_denied_provider(fixture_web):
    stack, _, _, _ = fixture_web
    with stack.browser_run() as run:
        stack.browser_probe(SETUP + FAKE_MAIN + r'''
mission=importlib.import_module('plugins.signal-scout.scout_runtime.mission')
plugin=get_plugin_manager()._plugins['signal-scout'].module
config['scout_mission'].update(research_model_requests=1,finalization_model_requests=1)
state=mission.MissionControl(mission.MissionRequest('Inspect fixture.'),config['scout_mission'])
with Browser(config) as browser:
    tab=open_fixture(browser);gate=browser.session(target_id=tab)
    def plan(index,kwargs):
        assert index<=2,'Denied logical request reached external provider'
        return response([tool({'action':'snapshot','args':{}},call_id='request-'+str(index))],content='')
    with fake_main(plan,iterations=8) as (agent,requests):
        agent._api_max_retries=1
        with adapter.bind_browser(gate,action_reference=action_reference,on_fatal=partial(agent.interrupt,hard_cancel=True),control=state), \
             plugin.model_requests.bind_model_requests(agent,max_attempts=1,control=state):
            native=agent.run_conversation('Inspect fixture, then report within budget. '+action_reference)
            result=state.finish(native)
            assert len(requests)==2 and len(state.requests)==2
            assert state.mode=='stopped' and agent._hard_interrupt_requested.is_set()
            assert mission.MissionOutcome.from_native(result)==mission.MissionOutcome.FAILURE
            assert 'Partial research report' in result['final_response']
            assert 'No completed findings available.' in result['final_response']
            assert native.get('interrupted') or native.get('failed') or not native.get('completed')
        stable_requests(requests)
        assert all('stream' not in request or request['stream'] is False for request in requests)
print('real native middleware cap denied provider dispatch and produced fallback')
''', run=run, timeout=180)
    assert run.container is None


def test_BR_003_BR_004_AG_006_MI_035_uncontained_native_failure_stops_before_report_inference(fixture_web):
    stack, _, _, _ = fixture_web
    with stack.browser_run() as run:
        stack.browser_probe(SETUP + FAKE_MAIN + r'''
with Browser(config) as browser:
    tab=open_fixture(browser);gate=browser.session(target_id=tab)
    native_execute=g.BrowserSession._execute
    def execute(self,*args,**kwargs):
        data=native_execute(self,*args,**kwargs)
        self._authority.contained=False
        return data
    def plan(index,kwargs):
        assert index==1,'Uncontained execution permitted report inference'
        return response([tool({'action':'snapshot','args':{}},call_id='uncontained')],content='')
    with patch.object(g.BrowserSession,'_execute',execute),fake_main(plan) as (agent,requests):
        stops=[]
        def stop():
            agent.interrupt(hard_cancel=True)
            stops.append((agent._interrupt_requested,agent._hard_interrupt_requested.is_set()))
        with adapter.bind_browser(gate,action_reference=action_reference,on_fatal=stop):
            native=agent.run_conversation('Inspect controlled fixture.')
        assert native.get('interrupted') and not native.get('completed')
        assert len(requests)==1,len(requests)
        assert stops==[(True,True)],stops
        feedback=model_result(tool_replies(native)[0])
        assert feedback['status']=='fatal' and feedback['data']['triage']['mission']=='stop',feedback
print('uncontained native control hard-stop before further inference verified')
''', run=run, timeout=180)
    assert run.container is None
