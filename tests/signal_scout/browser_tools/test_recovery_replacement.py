from tests.signal_scout.browser_tools.test_native_integration import SETUP
from tests.signal_scout.support.model_fixture import FAKE_MAIN
from repair_server import RecoveryServer


def test_AG_010_SR_005_owned_replacement_continues_native_conversation(fixture_web):
    stack, _, _, _ = fixture_web
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
            return response([tool({'action':'click','args':{'ref':ref}})],content='')
        result=model_result([m for m in kwargs['messages'] if m.get('role')=='tool'][-1])
        results.append(result)
        if index==2:
            assert result['status']=='incomplete',result
            repair=result['data']['recovery']
            assert repair['kind']=='restarted' and repair['generation']==2, result
            assert repair['refs_invalidated'] and repair['session_state_lost']
            assert repair['action_replayed'] is False
            assert browser.endpoint!=endpoint and browser.generation==2
            assert gate._selected!=tab and ref not in gate._refs
            assert browser._native_retired
            assert browser._generation_helper.finished.wait(15)
            helper=SUPERVISOR_REGISTRY.get(browser._generation_helper.task)
            assert helper is not None and helper.cdp_url==browser.endpoint
            deadline=time.monotonic()+10
            while SUPERVISOR_REGISTRY.get(initial_task) is not None:
                assert time.monotonic()<deadline
                time.sleep(.05)
            return response([tool({'action':'navigate','args':{'url':'http://public.test/evidence'}})],content='')
        if index==3:
            assert result['status']=='success',result
            fresh=reference(result['data']['snapshot'],'Below fold')
            assert fresh!=ref
            return response([tool({'action':'click','args':{'ref':fresh}})],content='')
        assert index==4 and result['status']=='success',result
        frames=gate._frames(gate._selected).frames
        root=next(frame for frame in frames.values() if frame.parent is None)
        assert gate._evaluate(root,'window.counts.click')['value']==1
        assert all(label in browser.sandbox_status for label in ('PID namespaces','Network namespaces','Seccomp-BPF sandbox'))
        return response()
    with fake_main(plan) as (agent,requests):
        agent._api_max_retries=1
        with adapter.bind_browser(gate,action_reference=action_reference,on_fatal=partial(agent.interrupt,hard_cancel=True)):
            outcome=agent.run_conversation('Continue through owned browser replacement without replaying old input.')
            assert outcome.get('completed') and not outcome.get('interrupted'),outcome
            assert len(results)==3
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
