from tests.signal_scout.support.mission import assert_clean, helper_state, browser_runs, receipt, wait_pending
import re
from pathlib import Path
import signal
import subprocess
import sys

import pytest

from tests.signal_scout.support.stack import lifecycle
from tests.signal_scout.support.diagnostics import saved_run, writer_identity, assert_writer_stopped

ROOT = Path(__file__).resolve().parents[3]
CLI = ROOT / "docker/signal-scout/mission_run.py"


def test_MI_035_actual_failed_cli_restores_both_stopped_helpers(cli_images, tmp_path):
    run = lifecycle.BrowserRun()
    before = set(lifecycle.checked(run.compose + ["ps", "--services", "--status", "running"]).splitlines())
    assert not before & {"proxy", "firewall"}, "Proof requires initially stopped helpers"
    result = subprocess.run([sys.executable, str(CLI), "--compose-file", str(lifecycle.COMPOSE),
        "--compose-file", str(cli_images), "CLI fixture startupfail"], capture_output=True, text=True, timeout=120)
    receipt(tmp_path, result)
    assert result.returncode == 1 and result.stdout == "", result.stderr
    assert "Offline fixture authentication failure" in result.stderr
    after = set(lifecycle.checked(run.compose + ["ps", "--services", "--status", "running"]).splitlines())
    assert not after & {"proxy", "firewall"}
    assert_clean(None)


def test_MI_032_actual_bootstrap_status_retains_bounded_alarm(cli_images, tmp_path):
    code = """import importlib, signal, sys
sys.path.insert(0,'/opt/hermes/plugins/signal-scout')
bootstrap=importlib.import_module('plugins.signal-scout.bootstrap')
sys.argv=['signal-scout','status']
bootstrap.main()
remaining,interval=signal.getitimer(signal.ITIMER_REAL)
assert 0 < remaining <= 120 and interval == 0
signal.alarm(0)
print('bounded-bootstrap-alarm-verified')
"""
    result = subprocess.run(["docker", "compose", "-f", str(lifecycle.COMPOSE), "-f", str(cli_images),
        "run", "--rm", "--no-deps", "-T", "--entrypoint", "python", "scout", "-c", code],
        text=True, capture_output=True, timeout=60)
    receipt(tmp_path, result)
    assert result.returncode == 0, result.stderr
    assert result.stdout.rstrip().endswith("bounded-bootstrap-alarm-verified")


def test_MI_032_MI_033_MI_034_MI_035_actual_cli_native_success_retry_and_verbose(cli_stack, tmp_path):
    """SR_023 SR_024"""
    stack, command, counts, baseline = cli_stack
    before = helper_state(stack)
    effects = counts().get("cli-effects", 0)
    stack.exec_proxy("from pathlib import Path; p=Path('/var/log/scout/browser.log'); f=p.open('a'); f.write('run=" + '0' * 32 + " origin=host previous-browser-run-marker\\n'); f.close()")
    result = subprocess.run([*command, "--verbose", "CLI fixture success"], text=True, capture_output=True, timeout=180)
    receipt(tmp_path, result)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Controlled research complete. One navigation and one click retained across native retry."
    text = result.stderr
    assert "browser ready (headless)" in text and "Research skill loaded" in text
    assert "model working" in text and "action proposed navigate" in text
    assert "model working (step 4)" in text
    assert "Scout review" not in text and "Reviewer" not in text
    assert "browser result click success" in text and "mission completed" in text
    assert text.count("Retrying API call in") == 1
    assert text.count("ValueError: fixture native multiline detail") == 1
    assert "Zx9AbCdEfGhIjKlMnOpQ" not in text and "fixture-debug-must-stay-hidden" not in text
    assert "signal_scout.proxy event=dns_failed" in text
    run_id = re.search(r'run=([0-9a-f]{32}) origin=browser component=launcher event=launcher_start', text).group(1)
    assert 'event=launcher_ready' in text and 'event=child_exit' in text and 'phase=after_stop_attempt' in text
    assert text.index('event=child_exit') < text.rindex('Scout mission completed.')
    assert text.index('Scout native conversation completed.') < text.index('event=child_exit')
    assert 'previous-browser-run-marker' not in text
    assert set(re.findall(r'run=([0-9a-f]{32}) origin=',text)) == {run_id}
    saved = saved_run(stack, run_id)
    assert 'event=child_exit' in saved and 'phase=pre_stop' in saved and 'phase=after_stop_attempt' in saved
    assert helper_state(stack) == before
    assert_clean(stack)
    assert counts().get("cli-effects", 0) == effects + 1


@pytest.mark.parametrize("scenario", ["exhaust", "fatal", "blocked"])
def test_MI_033_MI_035_actual_cli_failed_native_outcome_and_blocked_action(cli_stack, scenario, tmp_path):
    """SR_023 SR_024 MI_034"""
    stack, command, counts, _ = cli_stack
    before = helper_state(stack)
    effects = counts().get("cli-effects", 0)
    prior = browser_runs(stack)
    result = subprocess.run([*command, "CLI fixture " + scenario], text=True, capture_output=True, timeout=180)
    receipt(tmp_path, result)
    expected = 0 if scenario == "blocked" else 1
    assert result.returncode == expected, result.stderr
    assert result.stdout.strip() == ("Controlled blocked action preserved healthy conversation." if scenario == "blocked" else "")
    assert ("mission completed" if expected == 0 else "mission failed") in result.stderr
    if scenario == "fatal":
        assert "Browser action failed." in result.stderr
        assert "browser result navigate fatal" in result.stderr
        assert result.stderr.count("Scout model working") == 1
        assert counts().get("cli-effects", 0) == effects
    if scenario == "exhaust":
        assert "Broken pipe" in result.stderr
        assert result.stderr.count("Retrying API call in") == 5
    assert helper_state(stack) == before
    current = browser_runs(stack) - prior
    assert len(current) == 1
    saved = saved_run(stack, current.pop())
    assert 'event=launcher_start' in saved and 'event=child_exit' in saved
    assert 'phase=pre_stop' in saved and 'phase=after_stop_attempt' in saved
    assert 'event=launcher_start' not in result.stderr and 'event=launcher_ready' not in result.stderr
    assert_clean(stack)


@pytest.mark.parametrize('scenario,expected', [('budgetsuccess', 0), ('budgetexhaust', 1)])
def test_MI_036_actual_cli_mixed_invalid_response_uses_six_native_attempts(cli_stack, tmp_path, scenario, expected):
    stack, command, counts, _ = cli_stack
    before = helper_state(stack)
    effects = counts().get('cli-effects', 0)
    result = subprocess.run([*command, '--verbose', 'CLI fixture ' + scenario], text=True,
                            capture_output=True, timeout=180)
    receipt(tmp_path, result)
    assert result.returncode == expected, result.stderr
    admissions = re.findall(r'fixture application_attempt=(\d+) total=(\d+)', result.stderr)
    assert admissions == [(str(number), '6') for number in range(1, 7)], result.stderr
    if expected == 0:
        assert result.stdout.strip() == 'Controlled sixth native attempt succeeded.'
        assert 'application budget exhausted' not in result.stderr
    else:
        assert result.stdout == ''
        assert result.stderr.count('Scout model application budget exhausted') == 1
        assert 'attempts=6' in result.stderr and 'mission failed' in result.stderr
    assert counts().get('cli-effects', 0) == effects + 1
    assert helper_state(stack) == before
    assert_clean(stack)


@pytest.mark.parametrize('sink_failure', [False, True], ids=('healthy','killed-writer'))
def test_MI_035_actual_sigint_and_rejected_second_cli_preserve_active_owner(cli_stack, tmp_path, sink_failure):
    """SR_023 SR_024 MI_034"""
    stack, command, _, _ = cli_stack
    stack.compose("stop", "-t", "2", "firewall")
    before = helper_state(stack)
    child = subprocess.Popen([*command, "--verbose", "CLI fixture cancel"], text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    prefix = ""
    try:
        prefix = wait_pending(child)
        run_id = re.search(r'run=([0-9a-f]{32}) origin=browser component=launcher event=launcher_start', prefix).group(1)
        writer = writer_identity(stack, run_id)
        if sink_failure:
            stack.exec_proxy(f"import os,signal; os.kill({writer['pid']},signal.SIGKILL)")
        active = lifecycle.checked(["docker", "ps", "-q", "--no-trunc",
            "--filter", "label=com.docker.compose.project=signal-scout-bootstrap"])
        second = subprocess.run([*command, "CLI fixture success"], text=True, capture_output=True, timeout=30)
        assert second.returncode == 1 and second.stdout == ""
        assert lifecycle.checked(["docker", "ps", "-q", "--no-trunc",
            "--filter", "label=com.docker.compose.project=signal-scout-bootstrap"]) == active
        assert helper_state(stack) == {"proxy", "firewall"}
        child.send_signal(signal.SIGINT)
        output, error = child.communicate(timeout=30)
        assert child.returncode == 130 and output == "", prefix + error
        receipt(tmp_path, subprocess.CompletedProcess(command, child.returncode, output, prefix + error))
        assert "cancelled" in error
        assert 'event=child_exit' in error and 'phase=after_stop_attempt' in error
        saved = saved_run(stack, run_id)
        assert 'event=launcher_start' in saved and 'event=launcher_ready' in saved
        if sink_failure:
            assert 'Scout browser diagnostics unavailable or records dropped.' in error
        else:
            assert 'phase=pre_stop' in saved and 'phase=after_stop_attempt' in saved
        assert_writer_stopped(stack, run_id, writer)
    finally:
        if child.poll() is None:
            child.send_signal(signal.SIGINT)
            child.communicate(timeout=30)
    assert helper_state(stack) == before
    assert_clean(stack)
    stack.compose('up', '-d', '--wait', '--no-build', '--pull', 'never', '--force-recreate', 'proxy', 'firewall')
