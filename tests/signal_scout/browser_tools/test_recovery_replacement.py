from tests.signal_scout.browser_tools.test_native_integration import SETUP
from tests.signal_scout.support.model_fixture import FAKE_MAIN
from repair_server import RecoveryServer


def test_AG_010_SR_005_owned_replacement_continues_native_conversation(fixture_web):
    """BR_004 BR_006"""
    stack, _, counts, _ = fixture_web
    private_before = {key: value for key, value in counts().items() if key.startswith(('172.30.242.2:', '127.0.0.1:', '::1:'))}
    with stack.browser_run() as run, RecoveryServer(run) as server:
        original = run.container
        stack.browser_probe(SETUP + FAKE_MAIN + r'''
import os
from tools.browser_supervisor import SUPERVISOR_REGISTRY
assert os.getuid()==10000
assert not os.path.exists('/var/run/docker.sock')
with Browser(config) as browser:
    tab=open_fixture(browser);gate=browser.session(target_id=tab)
    ref=reference(gate._inspect_snapshot(tab),'Below fold')
    endpoint=browser.endpoint;initial_task=browser.task
    gate._permissions['screenshot']='blocked'
    rpc=[];native_rpc=browser.recovery.request
    def owner_request(*args,**kwargs):
        rpc.append(args[0]);return native_rpc(*args,**kwargs)
    browser.recovery.request=owner_request
    config['scout_recovery'].update(attempt_timeout=30,reconnect_attempts=1)
    config['browser']['request_timeout']=90
    killer=g._CDP(endpoint,2,1,.25)
    try:
        killer.command('Browser.close')
    except Exception:
        pass
    finally:
        killer.abort()
    results=[]
    def plan(index,kwargs):
        if index==1:
            return response([tool({'action':'click','args':{'ref':'css=#untrusted'}})],content='')
        result=model_result([m for m in kwargs['messages'] if m.get('role')=='tool'][-1],allow_guardrail=True)
        results.append(result)
        if index==2:
            assert result['status']=='rejected' and result['data']['error']['kind']=='request_format',result
            assert result['data']['action_reference']==action_reference and rpc==[]
            return response([tool({'action':'screenshot','args':{}})],content='')
        if index==3:
            assert result['status']=='rejected' and result['execution_started'] is False,result
            assert rpc==[] and browser.generation==1
            return response([tool({'action':'click','args':{'ref':ref}})],content='')
        if index==4:
            assert result['status']=='incomplete' and browser.generation==1,result
            assert result['data']['triage']['browser_use']=='unavailable'
            return response([tool({'action':'restart','args':{}},call_id='explicit-restart')],content='')
        if index==5:
            assert result['status']=='incomplete',result
            repair=result['data']['recovery']
            assert repair['kind']=='restarted' and repair['generation']==2, result
            assert repair['refs_invalidated'] and repair['session_state_lost']
            assert repair['action_replayed'] is False
            assert browser.endpoint!=endpoint and browser.generation==2
            assert gate._selected!=tab and ref not in gate._refs
            assert browser._native_retired
            assert browser._generation_helper.finished.wait(25)
            helper=SUPERVISOR_REGISTRY.get(browser._generation_helper.task)
            assert helper is not None and helper.cdp_url==browser.endpoint
            deadline=time.monotonic()+10
            while SUPERVISOR_REGISTRY.get(initial_task) is not None:
                assert time.monotonic()<deadline
                time.sleep(.05)
            return response([tool({'action':'navigate','args':{'url':'http://public.test/evidence'}})],content='')
        if index==6:
            assert result['status']=='success',result
            fresh=reference(result['data']['snapshot'],'Below fold')
            assert fresh!=ref
            return response([tool({'action':'click','args':{'ref':fresh}})],content='')
        if index==7:
            assert result['status']=='success',result
            observer=g._CDP(browser.endpoint,2,1,.25)
            try:
                selected=observer.command('Target.attachToTarget',{'targetId':gate._selected,'flatten':True})['sessionId']
                assert observer.command('Runtime.evaluate',{'expression':'window.counts.click',
                    'returnByValue':True},selected)['result']['value']==1
            finally:
                observer.close()
            assert all(label in browser.sandbox_status for label in ('PID namespaces','Network namespaces','Seccomp-BPF sandbox'))
            return response([tool({'action':'navigate','args':{'url':'http://public.test/attachment/gate-fixture.bin'}})],content='')
        if index==8:
            assert result['status']=='success',result
            assert result['data']['observation']['state']=='download_denied',result
            return response([tool({'action':'navigate','args':{'url':'http://private.test/'}})],content='')
        assert index==9 and result['status']=='incomplete',result
        assert browser.ready and not browser.failed
        return response()
    with fake_main(plan,iterations=11) as (agent,requests):
        agent._api_max_retries=1
        with adapter.bind_browser(gate,action_reference=action_reference,on_fatal=partial(agent.interrupt,hard_cancel=True)):
            outcome=agent.run_conversation('Continue through owned browser replacement without replaying old input.')
            assert outcome.get('completed') and not outcome.get('interrupted'),outcome
            assert len(results)==8
            stable_requests(requests)
print('owned generation replacement and native continuation verified')
''', *server.scout_options(), run=run, timeout=210)
        assert run.generation == 2 and run.container != original
        info = run.inspect_owned()
        destinations = {mount['Destination'] for mount in info['Mounts']}
        assert '/tmp/scout-recovery' not in destinations
        assert not any('auth' in destination or 'logs' in destination for destination in destinations)
        assert info['HostConfig']['Privileged'] is False
        assert not info['HostConfig']['PortBindings']
        from tests.signal_scout.support.stack import lifecycle
        lifecycle.checked(['docker', 'exec', run.container, 'python3', '-c',
            "from pathlib import Path; assert not [p for root in ('/tmp','/home') for p in Path(root).rglob('*') if p.name=='gate-fixture.bin' or p.suffix=='.crdownload']"])
        private_after = {key: value for key, value in counts().items() if key.startswith(('172.30.242.2:', '127.0.0.1:', '::1:'))}
        assert private_after == private_before


def test_BR_003_AG_006_AG_010_abandoned_replacement_cannot_touch_adopted_generation(fixture_web):
    stack, _, _, _ = fixture_web
    with stack.browser_run() as run, RecoveryServer(run) as server:
        stack.browser_probe(SETUP + FAKE_MAIN + r'''
with Browser(config) as browser:
    tab=open_fixture(browser);gate=browser.session(target_id=tab)
    stale=reference(gate._inspect_snapshot(tab),'Below fold')
    initial_endpoint=browser.endpoint
    config['browser']['request_timeout']=30
    config['scout_recovery'].update(attempt_timeout=60,reconnect_attempts=1)
    killer=g._CDP(browser.endpoint,2,1,.25)
    try:
        killer.command('Browser.close')
    except Exception:
        pass
    finally:
        killer.abort()
    parked=threading.Event();release=threading.Event();old=[];armed=[True];stops=[]
    native_init=g._CDP.__init__
    def init(self,*args,**kwargs):
        authority=kwargs.get('authority',args[4] if len(args)>4 else None)
        if authority is not None and '-restart-' in authority.call_id and armed[0]:
            armed[0]=False;old.append(authority);parked.set()
            assert release.wait(90),'Abandoned replacement attachment was not released'
        return native_init(self,*args,**kwargs)
    def plan(index,kwargs):
        if index==1:
            return response([tool({'action':'restart','args':{}},call_id='abandoned-replacement')],content='')
        result=model_result([m for m in kwargs['messages'] if m.get('role')=='tool'][-1])
        if index==2:
            assert parked.is_set() and result['status']=='incomplete',result
            assert result['data']['worker']['access_revoked'] is True
            assert old[0].revoked.is_set() and old[0].parent.revoked.is_set()
            assert browser.generation==1 and not stops
            config['browser']['request_timeout']=60
            return response([tool({'action':'restart','args':{}},call_id='adopt-current')],content='')
        if index==3:
            assert result['status']=='incomplete' and result['data']['recovery']['kind']=='restarted',result
            assert browser.generation==2 and browser.endpoint!=initial_endpoint
            assert stale not in gate._refs and gate._selected!=tab
            return response([tool({'action':'navigate','args':{'url':'http://public.test/evidence'}})],content='')
        if index==4:
            assert result['status']=='success',result
            return response([tool({'action':'click','args':{'ref':reference(result['data']['snapshot'],'Below fold')}})],content='')
        assert index==5 and result['status']=='success',result
        endpoint,selected,refs,helper=browser.endpoint,gate._selected,dict(gate._refs),browser._generation_helper
        release.set();assert old[0].done.wait(10) and old[0].parent.done.wait(10)
        assert browser.endpoint==endpoint and browser.generation==2 and browser._generation_helper is helper
        assert gate._selected==selected and gate._refs==refs and browser.ready and not browser.failed
        assert not stops
        observer=g._CDP(endpoint,2,1,.25)
        try:
            attached=observer.command('Target.attachToTarget',{'targetId':selected,'flatten':True})['sessionId']
            assert observer.command('Runtime.evaluate',{'expression':'window.counts.click',
                'returnByValue':True},attached)['result']['value']==1
        finally:
            observer.close()
        return response()
    try:
        with patch.object(g._CDP,'__init__',init),fake_main(plan) as (agent,requests):
            agent._api_max_retries=1
            def stop():
                stops.append(True);agent.interrupt(hard_cancel=True)
            with adapter.bind_browser(gate,action_reference=action_reference,on_fatal=stop):
                outcome=agent.run_conversation('Adopt current owned generation while abandoned replacement cannot regain authority.')
                assert outcome.get('completed') and not outcome.get('interrupted'),outcome
                assert not stops
                stable_requests(requests)
    finally:
        release.set()
''', *server.scout_options(), run=run, timeout=240)
        assert run.generation == 2
