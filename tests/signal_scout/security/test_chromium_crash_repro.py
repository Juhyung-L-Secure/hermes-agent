import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

import pytest
import yaml

from tests.signal_scout.support.stack import lifecycle
from tests.signal_scout.support.browser import SETUP


EVIDENCE = Path(os.environ.get('SCOUT_CHROMIUM_REPRO_EVIDENCE_DIR',
    str(Path(tempfile.gettempdir()) / 'scout-chromium-repro')))
CASE = {
    'name': 'headed-google-baseline',
    'url': 'https://www.google.com/search?q=why+rice+became+a+staple+food',
    'config': {'mode': 'headed'},
    'observation_deadline': 180,
}

RESOURCE_PROBE = r'''
import collections,json,os,time
from pathlib import Path
record={'monotonic':time.monotonic(),'filesystems':{},'processes':[],'cgroup':{}}
for name in ('/tmp','/dev/shm'):
    s=os.statvfs(name)
    record['filesystems'][name]={'bytes_total':s.f_blocks*s.f_frsize,'bytes_available':s.f_bavail*s.f_frsize,
        'bytes_free':s.f_bfree*s.f_frsize,'inodes_total':s.f_files,'inodes_free':s.f_ffree}
for name in ('memory.current','memory.max','memory.events','memory.events.local','memory.swap.current',
             'memory.swap.max','pids.current','pids.max','pids.events'):
    p=Path('/sys/fs/cgroup')/name
    if p.exists(): record['cgroup'][name]=p.read_text().strip()
for p in Path('/proc').iterdir():
    if not p.name.isdigit() or int(p.name)==os.getpid(): continue
    try:
        argv=p.joinpath('cmdline').read_bytes().decode(errors='replace').strip('\0').split('\0')
        status=dict(row.split(':',1) for row in p.joinpath('status').read_text().splitlines() if ':' in row)
        item={'pid':int(p.name),'comm':p.joinpath('comm').read_text().strip(),
              'status':{k:status[k].strip() for k in ('PPid','NSpid','State','VmRSS','VmSize','Threads','CapEff','Seccomp','NoNewPrivs') if k in status},
              'fd_count':len(list(p.joinpath('fd').iterdir())), 'limits':p.joinpath('limits').read_text()}
        if any('chrome' in arg for arg in argv[:1]):
            item['argv']=argv
        item['type']=next((arg for arg in argv if arg.startswith('--type=')),None)
        item['subtype']=next((arg for arg in argv if arg.startswith('--utility-sub-type=')),None)
        record['processes'].append(item)
    except (FileNotFoundError,ProcessLookupError,PermissionError): pass
record['crash_dumps']=[{'path':str(p),'size':p.stat().st_size} for p in Path('/tmp').rglob('*.dmp') if p.is_file()]
print(json.dumps(record))
'''


def command(directory, args, *, timeout=90):
    completed = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    with (directory / 'commands.jsonl').open('a') as handle:
        handle.write(json.dumps({'command':args,'exit':completed.returncode,
            'stdout':completed.stdout,'stderr':completed.stderr})+'\n')
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def log_metadata(directory, volume, tag):
    code = "from pathlib import Path; import json; print(json.dumps([{'name':p.name,'size':p.stat().st_size,'inode':p.stat().st_ino,'mtime_ns':p.stat().st_mtime_ns} for p in Path('/logs').iterdir() if p.is_file()]))"
    value = command(directory, ['docker','run','--rm','--network','none','--read-only','--cap-drop','ALL',
        '--security-opt','no-new-privileges:true','--user','10000:10000','--mount',
        'type=volume,src='+volume+',dst=/logs,readonly','--entrypoint','python3',
        'signal-scout-bootstrap-network:local','-c',code])
    (directory / ('canonical-logs-'+tag+'.json')).write_text(value+'\n')
    return json.loads(value)


@pytest.mark.linux_only
def test_headed_google_baseline():
    if os.environ.get('HERMES_E2E_BROWSER') != '1':
        pytest.skip('Explicit HERMES_E2E_BROWSER=1 required for Chromium crash investigation')
    EVIDENCE.mkdir(exist_ok=True, parents=True)
    directory = Path(tempfile.mkdtemp(prefix=CASE['name']+'-',dir=EVIDENCE))
    print('CHROMIUM_REPRO_EVIDENCE='+str(directory), flush=True)
    result = {'case':CASE,'navigation':None,'crash_fingerprint':{},'resources':[], 'cleanup':{}}
    (directory / 'test.sha256').write_text(hashlib.sha256(Path(__file__).read_bytes()).hexdigest()+'\n')
    original = lifecycle.BrowserRun()
    volumes = [original.model['volumes'][name]['name'] for name in ('scout-auth','scout-logs')]
    (directory / 'volumes-before.json').write_text(command(directory,['docker','volume','inspect',*volumes])+'\n')
    images = [original.model['services'][name]['image'] for name in ('scout','browser','proxy')]
    image_records = [json.loads(line) for line in command(directory,['docker','image','inspect',
        '--format','{"id":{{json .Id}},"tags":{{json .RepoTags}},"created":{{json .Created}}}',*images]).splitlines()]
    (directory / 'images.json').write_text(json.dumps(image_records,indent=2)+'\n')
    log_before = log_metadata(directory,volumes[1],'before')
    config = lifecycle.settings.load_settings(lifecycle.CONFIG)
    config['browser'].update(CASE['config'])
    path = directory / 'config.yaml'
    path.write_text(yaml.safe_dump(config))
    logs = 'scout-crash-repro-'+uuid.uuid4().hex+'-logs'
    override = directory / 'isolation.yaml'
    auth = next(v['source'] for v in original.model['services']['scout']['volumes'] if v['target']=='/var/lib/scout')
    override.write_text('configs:\n  scout-operating:\n    file: '+str(path)+'\nservices:\n  scout:\n    volumes: !override\n'
        '      - '+auth+':/var/lib/scout\n      - '+logs+':/var/lib/scout/logs\n'
        '  proxy:\n    volumes: !override\n      - '+logs+':/var/log/scout\n'
        '  firewall:\n    volumes: !override\n      - '+logs+':/var/log/scout\nvolumes:\n  '+logs+': {}\n')
    run = lifecycle.BrowserRun((lifecycle.COMPOSE,override),path,restore_helpers=True,verbose=True)
    child = None
    identity = None
    with (Path(tempfile.gettempdir())/'signal-scout-tests.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        running = set(command(directory,original.compose+['ps','--services','--status','running']).splitlines())
        try:
            with run:
                identity = run.container
                info = run.inspect_owned()
                (directory/'browser-identity.json').write_text(json.dumps({'id':info['Id'],'image':info['Image'],
                    'labels':info['Config']['Labels'],'state':info['State'],'resources':{k:info['HostConfig'].get(k)
                    for k in ('Memory','MemorySwap','PidsLimit','ShmSize','Tmpfs','ReadonlyRootfs','CapDrop','SecurityOpt','CpuQuota','NanoCpus')},
                    'mounts':info['Mounts']},indent=2)+'\n')
                probe = SETUP + '\nurl='+repr(CASE['url'])+'\n' + r'''
with Browser(config) as browser:
    print('REPRO_SANDBOX_VERIFIED',flush=True)
    evidence=browser.session()
    tab=evidence._tabs()[0]['target_id']
    gate=browser.session(target_id=tab)
    print('REPRO_NAVIGATION_STARTED',flush=True)
    outcome=dispatch(gate,'navigate',{'url':url},'Inspect public Google discovery page')
    print('REPRO_RESULT='+json.dumps({k:outcome[k] for k in ('status','reason','execution_started')}),flush=True)
'''
                (directory/'probe.py.txt').write_text(probe)
                options = [v for k,s in run.scout_environment().items() for v in ('-e',k+'='+s)]
                args = run.compose+['run','--rm','--no-deps','-T',*options,'--entrypoint','python','scout','-c',probe]
                (directory/'probe-command.json').write_text(json.dumps(args,indent=2)+'\n')
                started = time.monotonic()
                with (directory/'probe.stdout.txt').open('w') as out, (directory/'probe.stderr.txt').open('w') as err:
                    child = subprocess.Popen(args,stdout=out,stderr=err,text=True)
                    while child.poll() is None:
                        raw = command(directory,['docker','exec',identity,'python3','-c',RESOURCE_PROBE],timeout=10)
                        snapshot = json.loads(raw)
                        snapshot['elapsed'] = time.monotonic()-started
                        result['resources'].append(snapshot)
                        with (directory/'resources.jsonl').open('a') as handle:
                            handle.write(json.dumps(snapshot)+'\n')
                        assert time.monotonic()-started < CASE['observation_deadline'], 'Diagnostic observation deadline exceeded'
                        time.sleep(1)
                    result['probe_exit'] = child.returncode
                output = (directory/'probe.stdout.txt').read_text()
                records = [line.removeprefix('REPRO_RESULT=') for line in output.splitlines() if line.startswith('REPRO_RESULT=')]
                result['navigation'] = json.loads(records[-1]) if records else {'status':'probe_failed'}
                result['elapsed'] = time.monotonic()-started
                proxy = run.inspect_helper('proxy')['Id']
                code = "from pathlib import Path; print(''.join(p.read_text() for p in sorted(Path('/var/log/scout').glob('browser.log*')) if p.is_file()))"
                (directory/'browser-before-stop.log').write_text(command(directory,['docker','exec',proxy,'python3','-c',code])+'\n')
            code = "from pathlib import Path; print(''.join(p.read_text() for p in sorted(Path('/var/log/scout').glob('browser.log*')) if p.is_file()))"
            logs_text = command(directory,['docker','run','--rm','--network','none','--read-only','--cap-drop','ALL',
                '--security-opt','no-new-privileges:true','--user','10000:10000','--mount',
                'type=volume,src='+original.project+'_'+logs+',dst=/var/log/scout,readonly',
                '--entrypoint','python3','signal-scout-bootstrap-network:local','-c',code])
            (directory/'browser-final.log').write_text(logs_text+'\n')
            result['crash_fingerprint'] = {name:text in logs_text for name,text in {
                'mojo':'Failed to create a Mojo data pipe.', 'sigill':'Received signal 4 ILL_ILLOPN',
                'seccomp_alarm':'syscall nr=0x25','sigsegv':'Received signal 11'}.items()}
        finally:
            if child is not None and child.poll() is None:
                child.terminate()
                child.wait(timeout=20)
            command(directory,original.compose+['up','-d','--wait','--no-build','--pull','never','--force-recreate','proxy','firewall'])
            for service in ('firewall','proxy'):
                helper = original.inspect_helper(service)
                if service not in running and helper['State']['Running']:
                    command(directory,['docker','stop','-t',str(config['scout_lifecycle']['shutdown_timeout']),helper['Id']])
            volume = original.project+'_'+logs
            if command(directory,['docker','volume','ls','-q','--filter','name=^'+volume+'$']):
                command(directory,['docker','volume','rm',volume])
            leftovers = command(directory,['docker','ps','-aq','--filter','label=com.docker.compose.project='+original.project,
                '--filter','label=com.docker.compose.service=scout','--filter','label=com.docker.compose.service=browser'])
            result['cleanup']['scout_browser_absent'] = leftovers == ''
            result['cleanup']['helpers_running'] = sorted(set(command(directory,original.compose+['ps','--services','--status','running']).splitlines()))
            log_after = log_metadata(directory,volumes[1],'after')
            archives_before = {item['name']:item for item in log_before if not item['name'].endswith('.log')}
            archives_after = {item['name']:item for item in log_after if not item['name'].endswith('.log')}
            result['cleanup']['canonical_archive_metadata_unchanged'] = archives_after == archives_before
            current_after = {item['name']:item for item in log_after if item['name'].endswith('.log')}
            result['cleanup']['canonical_current_log_metadata_append_only'] = all(
                item['name'] in current_after
                and current_after[item['name']]['inode'] == item['inode']
                and current_after[item['name']]['size'] >= item['size']
                for item in log_before if item['name'].endswith('.log'))
            (directory/'volumes-after.json').write_text(command(directory,['docker','volume','inspect',*volumes])+'\n')
            result['cleanup']['run_lock_released'] = run.lock is None
            if run._diagnostics is not None:
                result['cleanup']['diagnostics_thread_stopped'] = not run._diagnostics.worker.is_alive()
                result['cleanup']['diagnostics_processes_stopped'] = all(p.poll() is not None for p in
                    (run._diagnostics.source,run._diagnostics.redactor,run._diagnostics.sink))
            (directory/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    assert result['cleanup']['scout_browser_absent']
    assert result['cleanup']['canonical_archive_metadata_unchanged']
    assert result['cleanup']['canonical_current_log_metadata_append_only']
    assert result['cleanup']['helpers_running'] == sorted(running)
    assert result['probe_exit'] == 0
    assert result['navigation']['status'] == 'success', result['navigation']
    assert not any(result['crash_fingerprint'].values()), result['crash_fingerprint']
