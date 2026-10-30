import json
import os
from pathlib import Path
import tempfile

from .settings import settings_digest


def clear_ready(service):
    Path('/tmp/' + service + '-ready').unlink(missing_ok=True)


def publish_ready(service, config):
    record = {'settings': settings_digest(config), 'pid': os.getpid(),
              'started': Path('/proc/self/stat').read_text().split()[21]}
    with tempfile.NamedTemporaryFile(mode='w', dir='/tmp', prefix=service + '-ready-', delete=False) as handle:
        temporary = Path(handle.name)
        try:
            json.dump(record, handle)
            handle.flush()
            temporary.replace('/tmp/' + service + '-ready')
        finally:
            temporary.unlink(missing_ok=True)


def consumed_settings(service):
    record = json.loads(Path('/tmp/' + service + '-ready').read_text())
    if Path('/proc/' + str(record['pid']) + '/stat').read_text().split()[21] != record['started']:
        raise ValueError('Stale Scout helper readiness.')
    return record['settings']
