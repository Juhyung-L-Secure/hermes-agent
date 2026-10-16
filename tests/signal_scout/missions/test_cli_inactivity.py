from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import time
import uuid

import pytest
import yaml

from tests.signal_scout.conftest import lifecycle
from tests.signal_scout.missions.test_cli_integration import (
    assert_clean,
    cli_images,
    cli_stack,
    fixture_web,
    helper_state,
)


@pytest.fixture(scope='module')
def inactivity_stack(cli_stack, cli_images, tmp_path_factory):
    stack, command, counts, baseline = cli_stack
    directory = tmp_path_factory.mktemp('scout-inactivity-inference')
    image = 'scout-inactivity-fixture-' + uuid.uuid4().hex + ':local'
    source = Path(__file__).with_name('inactivity_fixture.py')
    shutil.copyfile(source, directory / 'sitecustomize.py')
    shutil.copyfile(source.with_name('inference_fixture.py'), directory / 'inference_helpers.py')
    base = lifecycle.BrowserRun((lifecycle.COMPOSE, cli_images)).model['services']['scout']['image']
    (directory / 'Dockerfile').write_text('FROM ' + base + '\n'
        'COPY --chown=10000:10000 sitecustomize.py inference_helpers.py /opt/fixture/\n')
    lifecycle.checked(['docker', 'build', '-q', '-t', image, str(directory)], timeout=120)
    override = directory / 'inactivity.yaml'
    override.write_text(yaml.safe_dump({'services': {'scout': {'image': image}}}))
    try:
        yield stack, [*command, '--compose-file', str(override)], counts, baseline, image
    finally:
        lifecycle.checked(['docker', 'image', 'rm', image])


def owned_state():
    identifiers = lifecycle.checked(['docker', 'ps', '-aq', '--no-trunc', '--filter',
        'label=com.docker.compose.project=signal-scout-bootstrap']).splitlines()
    result = []
    for identifier in identifiers:
        inspection = subprocess.run(['docker', 'inspect', identifier], text=True, capture_output=True, timeout=10)
        if inspection.returncode:
            result.append({'id': identifier, 'missing_before_inspection': True, 'inspect_exit': inspection.returncode})
            continue
        info = json.loads(inspection.stdout)[0]
        service = info['Config']['Labels'].get('com.docker.compose.service')
        if service not in {'browser', 'scout'}:
            continue
        item = {'id': info['Id'], 'service': service, 'image': info['Image'], 'state': info['State']}
        if service == 'browser' and info['State']['Running']:
            code = """import json
from pathlib import Path
rows=[]
for path in Path('/proc').iterdir():
    if path.name.isdigit():
        try:
            name=(path/'comm').read_text().strip()
            if name in {'chrome','chromium','chrome_crashpad','python3','Xvfb','node'}:
                rows.append({'pid':int(path.name),'comm':name,'stat':(path/'stat').read_text().split()[2]})
        except OSError:
            pass
print(json.dumps(rows))
"""
            probe = subprocess.run(['docker', 'exec', identifier, 'python3', '-c', code],
                                   text=True, capture_output=True, timeout=10)
            item['process_probe_exit'] = probe.returncode
            item['processes'] = json.loads(probe.stdout) if probe.returncode == 0 else []
        result.append(item)
    return result


def replay_events(text):
    events = []
    for line in text.splitlines():
        if line.startswith('SCOUT_REPLAY '):
            events.append(json.loads(line.removeprefix('SCOUT_REPLAY ')))
    return events


def test_active_mission_keeps_native_session_through_inactivity_replay(inactivity_stack, tmp_path):
    stack, command, counts, baseline, image = inactivity_stack
    before = helper_state(stack)
    started = time.monotonic()
    receipt = {'started_utc': datetime.now(timezone.utc).isoformat(), 'command': [*command, '--verbose',
               'CLI inactivity replay'], 'inference_image': image, 'basetemp': str(tmp_path.parent),
               'helper_state_before': sorted(before), 'container_observations': []}
    (tmp_path / 'start.json').write_text(json.dumps(receipt, indent=2))
    print('Scout inactivity replay started ' + receipt['started_utc'] + '. Expected duration 4 minutes.', flush=True)
    stdout = tmp_path / 'stdout.txt'
    stderr = tmp_path / 'stderr.txt'
    observed = 0
    next_poll = 0
    with stdout.open('w') as output, stderr.open('w') as error:
        child = subprocess.Popen(receipt['command'], stdout=output, stderr=error, text=True)
        try:
            while child.poll() is None:
                elapsed = time.monotonic() - started
                assert elapsed < 600, 'CLI replay exceeded 600 seconds; inspect retained receipts'
                events = replay_events(stderr.read_text())
                fresh = events[observed:]
                observed = len(events)
                relevant = [event for event in fresh if event['event'] in {
                    'native-cleanup-start', 'native-cleanup-end', 'dispatch-start', 'dispatch-end',
                    'cdp-error', 'before-final-response'}]
                if elapsed >= next_poll or relevant:
                    snapshot = {'elapsed': round(elapsed, 3), 'trigger': [event['event'] for event in relevant],
                                'containers': owned_state()}
                    receipt['container_observations'].append(snapshot)
                    (tmp_path / 'observations.json').write_text(json.dumps(receipt, indent=2))
                    next_poll = elapsed + 10
                for event_name, marker in (('before-final-response', '/tmp/scout-replay-release'),
                                           ('cdp-error', '/tmp/scout-replay-error-release')):
                    if any(event['event'] == event_name for event in fresh):
                        scout = next((item for item in snapshot['containers'] if item.get('service') == 'scout'), None)
                        if scout:
                            acknowledgement = subprocess.run(['docker', 'exec', scout['id'], 'python', '-c',
                                'from pathlib import Path; Path(' + repr(marker) + ').touch()'],
                                capture_output=True, text=True, timeout=10)
                            receipt.setdefault('state_acknowledgements', []).append({
                                'event': event_name, 'exit': acknowledgement.returncode})
                time.sleep(0.25)
            child.wait(timeout=10)
        finally:
            if child.poll() is None:
                child.send_signal(__import__('signal').SIGINT)
                child.wait(timeout=40)
            receipt['exit'] = child.returncode
            receipt['elapsed'] = round(time.monotonic() - started, 3)
            (tmp_path / 'exit.txt').write_text(str(child.returncode) + '\n')
    text = stderr.read_text()
    events = replay_events(text)
    receipt['events'] = events
    receipt['helper_state_after'] = sorted(helper_state(stack))
    receipt['containers_after_cli'] = owned_state()
    receipt['fixture_counts_before'] = baseline
    receipt['fixture_counts_after'] = counts()
    (tmp_path / 'observations.json').write_text(json.dumps(receipt, indent=2))
    print('Scout inactivity replay receipts ' + str(tmp_path), flush=True)
    assert_clean(stack)
    assert helper_state(stack) == before
    assert child.returncode == 0, text
    assert stdout.read_text().strip() == 'Controlled inactivity replay completed.', text
    assert [event['status'] for event in events if event['event'] == 'dispatch-end'] == [
        'incomplete', 'success', 'success', 'success'], text
    assert [event['outcome'] for event in events if event['event'] == 'cdp-end'
            and event['method'] == 'Page.navigate'] == ['load_timeout', 'loaded', 'loaded'], text
    assert 'Non-streaming API call stale for 90s (threshold 90s)' in text, text
    assert text.count('Retrying API call in') == 1, text
    assert 'OpenAI client aborted (stale_call_kill, shared=False, tcp_force_closed=1,' in text, text
    assert any(event['event'] == 'loopback-native-abort' for event in events), text
    final_navigation = [event for event in events if event['event'] in {'cdp-start', 'cdp-end'}
                        and event['method'] == 'Page.navigate'][-2:]
    assert final_navigation[1]['elapsed'] - final_navigation[0]['elapsed'] >= 7.2, text
    premature = [event for event in events if event['event'] == 'native-cleanup-start'
                 and event['thread'] == 'browser-cleanup' and event['browser_ready'] is True]
    assert premature == [], 'Active mission lost native session to natural inactivity cleanup: ' + json.dumps(premature)
