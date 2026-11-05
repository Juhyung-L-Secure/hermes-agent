import pytest

from tests.signal_scout.browser_tools.test_native_integration import SETUP
from tests.signal_scout.support.model_fixture import FAKE_MAIN


def test_BR_003_BR_004_AG_006_native_timeout_and_acknowledged_observation_continue(fixture_web):
    stack, _, _, _ = fixture_web
    stack.browser_probe(SETUP + FAKE_MAIN + r'''
with Browser(config) as browser:
    tab=open_fixture(browser)
    gate=browser.session(target_id=tab)
    original=g._CDP.command;native_init=g._CDP.__init__
    sent=[];failing=[True]
    def command(self,method,params=None,session=None):
        if method=='Page.navigate': sent.append(params['url'])
        return original(self,method,params,session)
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
    def command(self,method,params=None,session=None):
        if position=='before_gate' and method=='Input.dispatchMouseEvent' and armed[0]:
            armed[0]=False;park(self.authority)
        return original_command(self,method,params,session)
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
        assert index==3 and result['status']=='success',result
        assert browser.command('eval','window.counts.click')['result']==1
        selected=gate._selected;refs=dict(gate._refs)
        release.set();assert old[0].done.wait(10)
        assert old[0].revoked.is_set()
        assert gate._selected==selected and gate._refs==refs
        assert browser.ready and not browser.failed
        assert browser.command('eval','window.counts.click')['result']==1
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
    native_dispatch=g.BrowserSession.dispatch
    def dispatch(self,proposal):
        result=native_dispatch(self,proposal)
        if self._authority is not None and armed[0]:
            armed[0]=False;old.append(self._authority);parked.set()
            assert release.wait(20)
            self._selected='abandoned-selected-target'
            self._refs.clear()
            self._browser.close()
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
        with patch.object(g.BrowserSession,'dispatch',dispatch),fake_main(plan) as (agent,requests):
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
    def command(self,method,params=None,session=None):
        if method=='Input.dispatchMouseEvent' and armed[0]:
            armed[0]=False;old.append(self.authority);parked.set()
            assert release.wait(20)
        return native_command(self,method,params,session)
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
