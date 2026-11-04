from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat


WSLG_RUNTIME = Path('/mnt/wslg/runtime-dir')
SOCKET = Path('/run/scout-wayland/wayland-0')


@dataclass(frozen=True)
class WaylandGrant:
    source: Path

    def validate(self):
        try:
            source = self.source.resolve(strict=True)
            if (source != self.source or source.parent != WSLG_RUNTIME.resolve(strict=True)
                    or re.fullmatch(r'wayland-[0-9]+', source.name) is None
                    or not stat.S_ISSOCK(source.stat().st_mode)):
                raise ValueError
        except (OSError, ValueError, RuntimeError):
            raise ValueError('Native headed debugging requires an available WSLg Wayland socket.') from None
        return self

    def mount(self):
        return {'type': 'bind', 'source': str(self.source), 'target': str(SOCKET),
                'read_only': True, 'bind': {'create_host_path': False}}


def resolve_wayland(environ=None):
    environ = os.environ if environ is None else environ
    display = environ.get('WAYLAND_DISPLAY')
    if not display:
        raise ValueError('Native headed debugging requires WAYLAND_DISPLAY from WSLg.')
    source = Path(display)
    if not source.is_absolute():
        runtime = environ.get('XDG_RUNTIME_DIR')
        if not runtime:
            raise ValueError('Native headed debugging requires XDG_RUNTIME_DIR from WSLg.')
        source = Path(runtime) / source
    try:
        source = source.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError('Native headed debugging requires an available WSLg Wayland socket.') from None
    return WaylandGrant(source).validate()


def child_environment(profile):
    runtime = Path(profile) / 'wayland-runtime'
    runtime.mkdir(mode=0o700)
    return {'WAYLAND_DISPLAY': str(SOCKET), 'XDG_RUNTIME_DIR': str(runtime)}
