from dataclasses import FrozenInstanceError
from pathlib import Path
import socket
import tempfile

import pytest
import yaml

from tests.signal_scout.support.stack import lifecycle

wayland = lifecycle.wayland


@pytest.fixture
def display(tmp_path, monkeypatch):
    with tempfile.TemporaryDirectory(prefix='scout-wl-') as directory:
        runtime = Path(directory) / 'runtime-dir'
        runtime.mkdir()
        monkeypatch.setattr(wayland, 'WSLG_RUNTIME', runtime)
        path = runtime / 'wayland-0'
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(str(path))
            yield path


def test_SR_011_SR_012_resolve_native_socket_and_reject_other_host_ipc(display, tmp_path):
    grant = wayland.resolve_wayland({'WAYLAND_DISPLAY':'wayland-0','XDG_RUNTIME_DIR':str(display.parent)})
    assert grant.source == display
    assert wayland.resolve_wayland({'WAYLAND_DISPLAY':str(display)}) == grant
    with pytest.raises(FrozenInstanceError):
        grant.source = tmp_path
    for values in ({}, {'WAYLAND_DISPLAY':'wayland-0'}, {'WAYLAND_DISPLAY':str(display.parent/'missing')}):
        with pytest.raises(ValueError, match='Native headed debugging requires'):
            wayland.resolve_wayland(values)
    other = display.parent.parent / 'docker.sock'
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(other))
        (display.parent/'wayland-1').symlink_to(other)
        for path in (other, display.parent/'wayland-1'):
            with pytest.raises(ValueError, match='available WSLg Wayland socket'):
                wayland.resolve_wayland({'WAYLAND_DISPLAY':str(path)})
    fake = display.parent/'wayland-2'
    fake.write_text('not a socket')
    with pytest.raises(ValueError, match='available WSLg Wayland socket'):
        wayland.resolve_wayland({'WAYLAND_DISPLAY':str(fake)})


def test_SR_011_SR_012_missing_display_fails_before_container_work(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, 'home', lambda:tmp_path)
    monkeypatch.delenv('WAYLAND_DISPLAY', raising=False)
    config = lifecycle.settings.load_settings(lifecycle.CONFIG)
    config['browser']['mode'] = 'headed'
    path = tmp_path/'config.yaml'
    path.write_text(yaml.safe_dump(config))
    run = lifecycle.BrowserRun(config_path=path)
    with pytest.raises(ValueError, match='requires WAYLAND_DISPLAY'):
        run._capture_configuration()
    assert not run._slot_path().exists()


def test_SR_011_SR_012_BT_006_capture_fixes_selected_mode_and_socket(display, monkeypatch, tmp_path):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('WAYLAND_DISPLAY', str(display))
    path = tmp_path / 'config.yaml'
    config = lifecycle.settings.load_settings(lifecycle.CONFIG)
    config['browser']['mode'] = 'headed'
    path.write_text(yaml.safe_dump(config))
    run = lifecycle.BrowserRun(config_path=path)
    try:
        run._capture_configuration()
        override = __import__('json').loads(run._config_override.read_text())
        assert override == {'configs': {'scout-operating': {'file': str(run._slot_path())}},
            'services': {'browser': {'volumes': [{'type': 'bind', 'source': str(display),
                'target': str(wayland.SOCKET), 'read_only': True, 'bind': {'create_host_path': False}}]}}}
        path.write_text('invalid source after capture')
        assert lifecycle.settings.load_settings(run._slot_path()) == config
    finally:
        run.close()


def test_SR_011_SR_012_container_environment_owns_private_runtime(display, monkeypatch, tmp_path):
    monkeypatch.setattr(wayland, 'SOCKET', display)
    profile=tmp_path/'profile'
    profile.mkdir()
    environment=wayland.child_environment(profile)
    assert environment=={'WAYLAND_DISPLAY':str(display),'XDG_RUNTIME_DIR':str(profile/'wayland-runtime')}
    assert (profile/'wayland-runtime').stat().st_mode & 0o777 == 0o700
