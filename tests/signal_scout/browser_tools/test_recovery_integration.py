import subprocess

import pytest

from tests.signal_scout.browser_tools.test_native_integration import SETUP
from tests.signal_scout.support.model_fixture import FAKE_MAIN


def test_BR_003_BR_004_AG_006_native_timeout_and_acknowledged_observation_continue(fixture_web):
    """AE_006"""
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + FAKE_MAIN + r'''
with Browser(config) as browser:
    tab=open_fixture(browser)
    gate=browser.session(target_id=tab)
    original=g._CDP.command;native_init=g._CDP.__init__
    sent=[];failing=[True]
    def command(self,method,params=None,session=None,**kwargs):
        if method=='Page.navigate': sent.append(params['url'])
        return original(self,method,params,session,**kwargs)
    def init(self,*args,**kwargs):
        native_init(self,*args,**kwargs);self.timeout=.25
        receive=self.connection.recv
        def recv(**kwargs):
            raw=receive(**kwargs);row=json.loads(raw)
            if self.facts.get('method')=='Runtime.evaluate' and self.effect_replies and failing[0] and row.get('id')==self.sequence:
                failing[0]=False;time.sleep(.35)
            return raw
        self.connection.recv=recv
    def plan(index,kwargs):
        if index==1:
            return response([tool({'action':'navigate','args':{'url':'http://public.test/evidence'}})],content='')
        result=model_result([m for m in kwargs['messages'] if m.get('role')=='tool'][-1])
        assert browser.ready and not browser.failed
        if index==2:
            assert result['status']=='incomplete',result
            assert result['data']['failure']['phase']=='observation',result
            assert result['data']['failure']['action_acknowledged'] is True
            assert result['data']['capture']['state']=='fresh'
            refs.append(reference(result['data']['snapshot'],'Below fold'))
            return response([tool({'action':'click','args':{'ref':refs[-1]}})],content='')
        assert index==3 and result['status']=='success',result
        assert browser.command('eval','window.counts.click')['result']==1
        return response()
    refs=[]
    with patch.object(g._CDP,'command',command),patch.object(g._CDP,'__init__',init),fake_main(plan) as (agent,requests):
        agent._api_max_retries=1
        with adapter.bind_browser(gate,action_reference=action_reference,on_fatal=partial(agent.interrupt,hard_cancel=True)):
            outcome=agent.run_conversation('Continue after failed observation; do not replay input.')
            assert outcome.get('completed') and not outcome.get('interrupted')
            assert sent==['http://public.test/evidence']
            stable_requests(requests)
''',timeout=150)


@pytest.mark.parametrize('position',['before_gate','before_send','inside_send'])
def test_BR_003_BR_004_AG_006_abandoned_send_cannot_affect_continuing_browser(fixture_web,position):
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + FAKE_MAIN + '\nposition='+repr(position)+'\n'+r'''
with Browser(config) as browser:
    tab=open_fixture(browser);gate=browser.session(target_id=tab)
    config['browser']['request_timeout']=2
    parked=threading.Event();release=threading.Event();armed=[True];old=[]
    original_command=g._CDP.command
    original_init=g._CDP.__init__
    def park(authority):
        old.append(authority);parked.set()
        assert release.wait(20),'Old request was never released'
    def command(self,method,params=None,session=None,**kwargs):
        if position=='before_gate' and method=='Input.dispatchMouseEvent' and armed[0]:
            armed[0]=False;park(self.authority)
        return original_command(self,method,params,session,**kwargs)
    def init(self,*args,**kwargs):
        original_init(self,*args,**kwargs)
        native=self.connection.send
        if position=='before_send':
            def send(raw):
                row=json.loads(raw)
                if row['method']=='Input.dispatchMouseEvent' and armed[0]:
                    armed[0]=False;park(self.authority)
                return native(raw)
            self.connection.send=send
        elif position=='inside_send':
            raw_socket=self.connection.socket
            authority=self.authority
            current=[False]
            class Socket:
                def __getattr__(self,name): return getattr(raw_socket,name)
                def sendall(self,data):
                    if current[0] and armed[0]:
                        armed[0]=False;park(authority)
                    return raw_socket.sendall(data)
            self.connection.socket=Socket()
            def send(raw):
                current[0]=json.loads(raw)['method']=='Input.dispatchMouseEvent'
                try: return native(raw)
                finally: current[0]=False
            self.connection.send=send
    def plan(index,kwargs):
        if index==1:
            return response([tool({'action':'click','args':{'ref':ref}},call_id='old')],content='')
        result=model_result([m for m in kwargs['messages'] if m.get('role')=='tool'][-1])
        if index==2:
            assert parked.is_set() and result['status']=='incomplete',result
            assert result['data']['worker']=={'cancel_requested':True,'access_revoked':True,'termination_confirmed':False}
            assert browser.command('eval','window.counts.click')['result']==0
            return response([tool({'action':'click','args':{'ref':ref}},call_id='fresh')],content='')
        assert index==3 and result['status']==('success' if position=='before_gate' else 'rejected'),result
        expected=1 if position=='before_gate' else 0
        assert browser.command('eval','window.counts.click')['result']==expected
        selected=gate._selected;refs=dict(gate._refs)
        release.set();assert old[0].done.wait(10)
        assert old[0].revoked.is_set()
        assert gate._selected==selected and gate._refs==refs
        assert browser.ready and not browser.failed
        assert browser.command('eval','window.counts.click')['result']==expected
        return response()
    ref=reference(gate._inspect_snapshot(tab),'Below fold')
    try:
        with patch.object(g._CDP,'command',command),patch.object(g._CDP,'__init__',init),fake_main(plan) as (agent,requests):
            agent._api_max_retries=1
            with adapter.bind_browser(gate,action_reference=action_reference,on_fatal=partial(agent.interrupt,hard_cancel=True)):
                outcome=agent.run_conversation('An abandoned click must never reach the page.')
                assert outcome.get('completed') and not outcome.get('interrupted')
                stable_requests(requests)
    finally:
        release.set()
''',timeout=150)


def test_BR_004_AG_006_delayed_reply_is_uncertain_and_does_not_repair(fixture_web):
    """AE_006"""
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + FAKE_MAIN + r'''
with Browser(config) as browser:
    tab=open_fixture(browser);gate=browser.session(target_id=tab)
    native_init=g._CDP.__init__;sent=[];armed=[True]
    def init(self,*args,**kwargs):
        native_init(self,*args,**kwargs)
        self.timeout=.25
        native_send=self.connection.send;native_recv=self.connection.recv
        def send(raw):
            row=json.loads(raw)
            if row['method']=='Page.navigate': sent.append(row['params']['url'])
            return native_send(raw)
        def recv(**kwargs):
            raw=native_recv(**kwargs);row=json.loads(raw)
            if self.facts.get('method')=='Page.navigate' and row.get('id')==self.sequence and armed[0]:
                armed[0]=False;time.sleep(.35)
            return raw
        self.connection.send=send;self.connection.recv=recv
    endpoint=browser.endpoint
    def plan(index,kwargs):
        if index==1:
            return response([tool({'action':'navigate','args':{'url':'http://public.test/evidence'}})],content='')
        result=model_result([m for m in kwargs['messages'] if m.get('role')=='tool'][-1])
        assert browser.endpoint==endpoint and browser.ready and not browser.failed
        if index==2:
            assert result['status']=='incomplete',result
            failure=result['data']['failure']
            assert failure['kind']=='command_timeout' and failure['phase']=='execution'
            assert failure['command']['method']=='Page.navigate'
            assert failure['command']['sent'] is True and failure['command']['response_received'] is False
            assert failure['action_acknowledged'] is False and failure['remote_effects']=='unknown'
            assert result['data']['capture']['state']=='fresh'
            assert 'Below fold' in result['data']['snapshot']
            return response([tool({'action':'snapshot','args':{}})],content='')
        assert index==3 and result['status']=='success',result
        assert sent==['http://public.test/evidence']
        return response()
    with patch.object(g._CDP,'__init__',init),fake_main(plan) as (agent,requests):
        agent._api_max_retries=1
        with adapter.bind_browser(gate,action_reference=action_reference,on_fatal=partial(agent.interrupt,hard_cancel=True)):
            outcome=agent.run_conversation('Inspect uncertainty without automatic repair or replay.')
            assert outcome.get('completed') and not outcome.get('interrupted')
            stable_requests(requests)
''',timeout=150)


def test_BR_003_BR_004_AG_006_late_fatal_state_and_cleanup_are_private(fixture_web):
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + FAKE_MAIN + r'''
with Browser(config) as browser:
    tab=open_fixture(browser);gate=browser.session(target_id=tab)
    config['browser']['request_timeout']=2
    parked=threading.Event();release=threading.Event();old=[];armed=[True]
    native_dispatch=g.BrowserSession._dispatch
    def dispatch(self,proposal):
        result=native_dispatch(self,proposal)
        if self._authority is not None and armed[0]:
            armed[0]=False;old.append(self._authority);parked.set()
            assert release.wait(20)
            self._selected='abandoned-selected-target'
            self._refs.clear()
            self._disconnect()
            return self._result('fatal','Abandoned failure must not stop current mission.')
        return result
    def plan(index,kwargs):
        if index==1:
            return response([tool({'action':'snapshot','args':{}},call_id='same-id')],content='')
        result=model_result([m for m in kwargs['messages'] if m.get('role')=='tool'][-1])
        if index==2:
            assert parked.is_set() and result['status']=='incomplete',result
            return response([tool({'action':'click','args':{'ref':ref}},call_id='same-id')],content='')
        assert index==3 and result['status']=='success',result
        refs=dict(gate._refs);selected=gate._selected
        release.set();assert old[0].done.wait(10)
        assert gate._refs==refs and gate._selected==selected==tab
        assert browser.ready and not browser.failed
        assert browser.command('eval','window.counts.click')['result']==1
        return response()
    ref=reference(gate._inspect_snapshot(tab),'Below fold')
    try:
        with patch.object(g.BrowserSession,'_dispatch',dispatch),fake_main(plan) as (agent,requests):
            agent._api_max_retries=1
            with adapter.bind_browser(gate,action_reference=action_reference,on_fatal=partial(agent.interrupt,hard_cancel=True)):
                outcome=agent.run_conversation('Continue despite late abandoned failure.')
                assert outcome.get('completed') and not outcome.get('interrupted')
                stable_requests(requests)
    finally:
        release.set()
''',timeout=150)


def test_BR_003_BR_004_AG_006_native_backstop_revokes_exact_request(fixture_web):
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + FAKE_MAIN + r'''
with Browser(config) as browser:
    tab=open_fixture(browser);gate=browser.session(target_id=tab)
    config['browser']['request_timeout']=20
    parked=threading.Event();release=threading.Event();old=[];armed=[True]
    native_command=g._CDP.command
    def command(self,method,params=None,session=None,**kwargs):
        if method=='Input.dispatchMouseEvent' and armed[0]:
            armed[0]=False;old.append(self.authority);parked.set()
            assert release.wait(20)
        return native_command(self,method,params,session,**kwargs)
    ref=reference(gate._inspect_snapshot(tab),'Below fold')
    def plan(index,kwargs):
        if index==1:
            return response([tool({'action':'click','args':{'ref':ref}},call_id='reused-id')],content='')
        previous=[m for m in kwargs['messages'] if m.get('role')=='tool'][-1]
        if index==2:
            assert parked.is_set() and 'timed out after 2.0s' in previous['content'],previous
            assert old[0].revoked.is_set(), 'Native observer did not revoke abandoned authority'
            return response([tool({'action':'click','args':{'ref':ref}},call_id='reused-id')],content='')
        assert index==3 and model_result(previous)['status']=='success',previous
        assert browser.command('eval','window.counts.click')['result']==1
        release.set();assert old[0].done.wait(10)
        deadline=time.monotonic()+5
        while any(item.parent_tid==old[0].parent_tid for item in adapter._invocations):
            assert time.monotonic()<deadline,'Abandoned native handler did not finish owned cleanup'
            time.sleep(.01)
        assert browser.ready and not browser.failed
        assert browser.command('eval','window.counts.click')['result']==1
        return response()
    try:
        with patch.object(g._CDP,'command',command),patch('agent.tool_executor._resolve_sequential_tool_timeout',return_value=2),fake_main(plan) as (agent,requests):
            agent._api_max_retries=1
            with adapter.bind_browser(gate,action_reference=action_reference,on_fatal=partial(agent.interrupt,hard_cancel=True)):
                outcome=agent.run_conversation('Continue after native timeout with same call ID.')
                assert outcome.get('completed') and not outcome.get('interrupted')
                stable_requests(requests)
    finally:
        release.set()
''',timeout=150)


def test_BR_003_AG_006_whole_deadline_includes_owned_cleanup(fixture_web):
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + FAKE_MAIN + r'''
with Browser(config) as browser:
    tab=open_fixture(browser);gate=browser.session(target_id=tab)
    config['browser']['request_timeout']=2
    parked=threading.Event();release=threading.Event();old=[];armed=[True]
    native_close=g._CDP.close
    def close(self):
        if self.authority is not None and armed[0]:
            armed[0]=False;old.append(self.authority);parked.set()
            assert release.wait(20)
        return native_close(self)
    ref=reference(gate._inspect_snapshot(tab),'Below fold')
    def plan(index,kwargs):
        if index==1:
            return response([tool({'action':'snapshot','args':{}},call_id='cleanup-old')],content='')
        result=model_result([m for m in kwargs['messages'] if m.get('role')=='tool'][-1])
        if index==2:
            assert parked.is_set() and result['status']=='incomplete',result
            assert result['data']['failure']['phase']=='cleanup'
            return response([tool({'action':'click','args':{'ref':ref}},call_id='cleanup-new')],content='')
        assert index==3 and result['status']=='success',result
        selected=gate._selected;refs=dict(gate._refs)
        release.set();assert old[0].done.wait(10)
        assert gate._selected==selected==tab and gate._refs==refs
        assert browser.ready and not browser.failed
        assert browser.command('eval','window.counts.click')['result']==1
        return response()
    try:
        with patch.object(g._CDP,'close',close),fake_main(plan) as (agent,requests):
            agent._api_max_retries=1
            with adapter.bind_browser(gate,action_reference=action_reference,on_fatal=partial(agent.interrupt,hard_cancel=True)):
                outcome=agent.run_conversation('Allow abandoned cleanup without closing current browser.')
                assert outcome.get('completed') and not outcome.get('interrupted')
                stable_requests(requests)
    finally:
        release.set()
''',timeout=150)


def test_BR_004_AG_010_confirmed_loss_reconnects_same_browser_without_replay(fixture_web):
    stack, _, _, _ = fixture_web
    with stack.browser_run() as run:
        owned = run.container
        stack.browser_probe(SETUP + FAKE_MAIN + r'''
with Browser(config) as browser:
    tab=open_fixture(browser);gate=browser.session(target_id=tab)
    original=g._CDP.command;armed=[True];events=[];old_ref=reference(gate._inspect_snapshot(tab),'Below fold')
    endpoint=browser.endpoint;task=browser.task
    def command(self,method,params=None,session=None,**kwargs):
        if self.authority is not None and method=='DOM.getContentQuads' and armed[0]:
            armed[0]=False;self.connection.close()
        if self.authority is not None and method in {'Page.navigate','Page.reload'}:
            events.append(method)
        return original(self,method,params,session,**kwargs)
    def plan(index,kwargs):
        if index==1:
            return response([tool({'action':'click','args':{'ref':old_ref}})],content='')
        result=model_result([m for m in kwargs['messages'] if m.get('role')=='tool'][-1])
        assert browser.ready and not browser.failed and browser.endpoint==endpoint and browser.task==task
        if index==2:
            assert result['status']=='incomplete',result
            assert result['data']['failure']['kind']=='control_lost'
            assert result['data']['recovery']=={'kind':'reconnected','reconnect_attempts':1,'restart_attempts':0,
                'refs_invalidated':True,'session_state_lost':False,'action_replayed':False}
            assert result['data']['capture']['state']=='fresh' and result['data']['target_id']==tab
            fresh.append(reference(result['data']['snapshot'],'Below fold'))
            assert fresh[0]!=old_ref
            assert browser.command('eval','window.counts.click')['result']==0
            return response([tool({'action':'click','args':{'ref':old_ref}})],content='')
        if index==3:
            assert result['status']=='rejected' and result['execution_started'] is False,result
            return response([tool({'action':'click','args':{'ref':fresh[0]}})],content='')
        assert index==4 and result['status']=='success',result
        assert browser.command('eval','window.counts.click')['result']==1
        assert events==[]
        return response()
    fresh=[]
    with patch.object(g._CDP,'command',command),fake_main(plan) as (agent,requests):
        agent._api_max_retries=1
        with adapter.bind_browser(gate,action_reference=action_reference,on_fatal=partial(agent.interrupt,hard_cancel=True)):
            outcome=agent.run_conversation('Reconnect controlled browser; reject obsolete refs and never replay input.')
            assert outcome.get('completed') and not outcome.get('interrupted')
            stable_requests(requests)
''',run=run,timeout=150)
        assert run.container == owned


@pytest.mark.parametrize('case', ['close', 'fatal'])
def test_BR_003_AG_006_real_owner_cleanup_is_bounded_and_stop_precedes_cleanup(fixture_web, case):
    """AG_005"""
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + FAKE_MAIN + '\ncase='+repr(case)+'\n'+r'''
from contextlib import nullcontext
lifetime=importlib.import_module('plugins.signal-scout.scout_runtime.browser.lifetime')
errors=importlib.import_module('plugins.signal-scout.scout_runtime.browser._errors')
with Browser(config) as browser:
    tab=open_fixture(browser);gate=browser.session(target_id=tab)
    config['browser']['request_timeout']=2
    parked=threading.Event();release=threading.Event();old=[];stops=[];ordering=[]
    native_close=browser.close
    def close():
        ordering.append(bool(stops))
        old.append(gate._requests.active)
        parked.set()
        assert release.wait(20),'Root cleanup was not released'
        return native_close()
    def broken_control(self):
        self.authority.contained=False
        raise errors.ControlUnavailable('Controlled containment failure.')
    def plan(index,kwargs):
        if index==1:
            return response([tool({'action':'close' if case=='close' else 'snapshot','args':{}})],content='')
        assert case=='close' and index==2,'Required fatal stop allowed another model call'
        result=model_result([m for m in kwargs['messages'] if m.get('role')=='tool'][-1])
        assert result['status']=='incomplete' and result['data']['failure']['phase']=='cleanup',result
        assert result['data']['triage']['browser_use']=='closed',result
        assert result['data']['triage']['allowed_operations']==[],result
        assert result['data']['triage']['mission']=='report_only',result
        assert parked.is_set() and not release.is_set()
        return response([tool({'action':'snapshot','args':{}},call_id='must-reject')],content='')
    try:
        with fake_main(plan) as (agent,requests),patch.object(browser,'close',close):
            agent._api_max_retries=1
            def stop():
                stops.append(True);agent.interrupt(hard_cancel=True)
            with patch.object(lifetime._RequestBrowser,'verify_control',broken_control) if case=='fatal' else nullcontext():
                with adapter.bind_browser(gate,action_reference=action_reference,on_fatal=stop):
                    began=time.monotonic()
                    outcome=agent.run_conversation('Stop terminal browsing without waiting for owned cleanup.')
                    assert time.monotonic()-began<8,'Real owner cleanup escaped whole request deadline'
                    assert outcome.get('interrupted') and not outcome.get('completed')
                    assert len(requests)==(1 if case=='fatal' else 2)
                    assert parked.is_set() and stops==[True]
                    assert ordering==[case=='fatal'],'Fatal callback waited behind real owner cleanup'
                    assert gate._requests.closed and old[0].revoked.is_set()
                    rejection=unpack(dispatch({'action':'snapshot','args':{}}))
                    assert rejection['status']=='fatal' and not release.is_set()
                release.set();assert old[0].done.wait(10)
                assert stops==[True],'Old cleanup issued a late fatal callback'
    finally:
        release.set()
''', timeout=150)


def test_BR_003_AG_005_AG_006_MI_035_native_completed_close_allows_final_answer(fixture_web):
    stack, _, _, _ = fixture_web
    with stack.browser_run() as run:
        owned = run.container
        stack.browser_probe(SETUP + FAKE_MAIN + r'''
mission=importlib.import_module('plugins.signal-scout.scout_runtime.mission')
plugin=get_plugin_manager()._plugins['signal-scout'].module
state=mission.MissionControl(mission.MissionRequest('Retain completed findings.'),config['scout_mission'])
with Browser(config) as browser:
    gate=browser.session()
    stops=[]
    def plan(index,kwargs):
        if index==1:
            return response([tool({'action':'close','args':{}},call_id='completed-close')],content='')
        assert index==2,'Completed close admitted an extra model request'
        result=model_result([m for m in kwargs['messages'] if m.get('role')=='tool'][-1])
        assert result['status']=='success' and result['reason'] is None,result
        assert result['execution_started'] is True and result['data']['closed'] is True
        assert result['data']['triage']=={'browser_use':'closed','mission':'continue',
            'allowed_operations':[],'operational_failure':False,'secondary_failures':[]}
        assert gate._requests.closed and not browser.ready and browser.failed
        return response(content='Completed findings retained after browser close.')
    with fake_main(plan) as (agent,requests):
        agent._api_max_retries=1
        def stop():
            stops.append(True);agent.interrupt(hard_cancel=True)
        with adapter.bind_browser(gate,action_reference=action_reference,on_fatal=stop,control=state), \
             plugin.model_requests.bind_model_requests(agent,max_attempts=1,control=state):
            native=agent.run_conversation('Close completed browsing, then return verified findings. '+action_reference)
            result=state.finish(native)
            assert mission.MissionOutcome.from_native(result)==mission.MissionOutcome.SUCCESS,result
            assert result['final_response']=='Completed findings retained after browser close.'
            assert native.get('completed') and not native.get('interrupted') and not result.get('scout_incomplete')
            assert state.mode=='research' and stops==[] and len(requests)==2
            stable_requests(requests)
print('completed native close preserves final answer without browser permission')
''', run=run, timeout=150)
    assert run.container is None
    inspected = subprocess.run(['docker', 'inspect', owned], capture_output=True, timeout=10)
    assert inspected.returncode != 0 and b'no such object' in inspected.stderr.lower()
