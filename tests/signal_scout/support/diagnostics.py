import json
from pathlib import Path
import subprocess
import sys
import textwrap

import yaml

ROOT = Path(__file__).resolve().parents[3]


def saved_run(stack, run):
    return stack.exec_proxy(f"""
from pathlib import Path
p = Path('/var/log/scout/browser.log')
print('\\n'.join(line for path in p.parent.glob('browser.log*') if path.is_file()
                for line in path.read_text().splitlines() if {run!r} in line))
""")


def writer_identity(stack, run):
    path = '/tmp/scout-browser-writer-' + run + '.json'
    return json.loads(stack.exec_proxy(f"from pathlib import Path; print(Path({path!r}).read_text())"))


def assert_writer_stopped(stack, run, writer):
    marker = '/tmp/scout-browser-writer-' + run + '.json'
    pid, start = writer['pid'], writer['start']
    stack.exec_proxy(f"""
from pathlib import Path
assert not Path({marker!r}).exists()
path=Path('/proc/{pid}/stat')
if path.exists():
    fields=path.read_text().rsplit(')',1)[1].split()
    assert fields[0]=='Z' or fields[19]!={start!r}
""")

def isolated(tmp_path, code):
    config = yaml.safe_load((ROOT / 'docker/signal-scout/config.yaml').read_text())
    config['logging'] = {'level': 'INFO', 'max_size_mb': 1, 'backup_count': 2}
    (tmp_path / 'config.yaml').write_text(yaml.safe_dump(config))
    result = subprocess.run([sys.executable, '-c', textwrap.dedent(code)], cwd=ROOT,
                            env={'HERMES_HOME': str(tmp_path), 'HOME': str(tmp_path),
                                 'PYTHONPATH': str(ROOT), 'PATH': '/usr/bin:/bin'},
                            text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr[-2000:]
    return result
