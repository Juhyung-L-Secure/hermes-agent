import copy
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
        run._fix_configuration()
    assert run._run_config is None and not run._slot_path().exists()


def test_SR_011_SR_012_BT_006_frozen_mode_and_socket_drive_both_mount_guards(display, monkeypatch, tmp_path):
    monkeypatch.setattr(Path, 'home', lambda:tmp_path)
    monkeypatch.setenv('WAYLAND_DISPLAY',str(display))
    path=tmp_path/'config.yaml'
    path.write_bytes(lifecycle.CONFIG.read_bytes())
    run=lifecycle.BrowserRun(config_path=path)
    config=lifecycle.settings.load_settings(path)
    config['browser']['mode']='headed'
    path.write_text(yaml.safe_dump(config))
    try:
        run._fix_configuration()
        grant=run._run_config.wayland
        assert grant.source==display
        assert run.config['browser']['mode']=='headed'
        lifecycle.validate_config_grants(run.model,run._run_config.path,grant)
        mounts=[{'Type':'bind','Source':str(run._run_config.path),'Destination':'/opt/scout/config.yaml','RW':False},
                {'Type':'bind','Source':str(display),'Destination':str(wayland.SOCKET),'RW':False}]
        run._verify_config_mount({'Mounts':mounts},run._run_config,'browser')
        monkeypatch.setenv('WAYLAND_DISPLAY',str(tmp_path/'different'))
        path.write_text('invalid source after capture')
        run._verify_config_mount({'Mounts':mounts},run._run_config,'browser')
        for service in ('scout','proxy','firewall'):
            with pytest.raises(RuntimeError,match='display delivery mismatch'):
                run._verify_config_mount({'Mounts':mounts},run._run_config,service)
        for fault in ('source','writable','missing','extra','duplicate'):
            changed=copy.deepcopy(mounts)
            if fault=='source': changed[1]['Source']='/var/run/docker.sock'
            elif fault=='writable': changed[1]['RW']=True
            elif fault=='missing': changed.pop()
            elif fault=='duplicate': changed.append(dict(changed[1]))
            else: changed.append({'Type':'bind','Source':'/tmp','Destination':'/other','RW':False})
            with pytest.raises((RuntimeError,ValueError),match='delivery mismatch'):
                run._verify_config_mount({'Mounts':changed},run._run_config,'browser')
        for fault in ('source','writable','missing','extra','other-service','create-path'):
            model=copy.deepcopy(run.model)
            mount=model['services']['browser']['volumes'][0]
            if fault=='source': mount['source']='/var/run/docker.sock'
            elif fault=='writable': mount['read_only']=False
            elif fault=='missing': model['services']['browser']['volumes']=[]
            elif fault=='create-path': mount['bind']['create_host_path']=True
            elif fault=='other-service': model['services']['scout']['volumes'].append(mount)
            else: model['services']['browser']['volumes'].append({'type':'bind','source':'/tmp','target':'/other'})
            with pytest.raises(ValueError,match='delivery mismatch'):
                lifecycle.validate_config_grants(model,run._run_config.path,grant)
        with pytest.raises(ValueError,match='display delivery mismatch'):
            lifecycle.validate_config_grants(run.model,run._run_config.path)
    finally:
        run.close()


def test_SR_011_SR_012_container_environment_owns_private_runtime(display, monkeypatch, tmp_path):
    monkeypatch.setattr(wayland, 'SOCKET', display)
    profile=tmp_path/'profile'
    profile.mkdir()
    environment=wayland.child_environment(profile)
    assert environment=={'WAYLAND_DISPLAY':str(display),'XDG_RUNTIME_DIR':str(profile/'wayland-runtime')}
    assert (profile/'wayland-runtime').stat().st_mode & 0o777 == 0o700
    display.unlink()
    with pytest.raises(RuntimeError,match='display socket unavailable'):
        wayland.child_environment(profile)
