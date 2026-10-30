
import importlib.util
from pathlib import Path
import subprocess
import textwrap


ROOT = Path(__file__).resolve().parents[3]
COMPOSE = ["docker", "compose", "-f", str(ROOT / "docker/signal-scout/compose.yaml")]
_spec = importlib.util.spec_from_file_location("scout_browser_run", ROOT / "docker/signal-scout/browser_run.py")
lifecycle = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lifecycle)


class ScoutStack:

    browser_compose_files = (lifecycle.COMPOSE,)
    browser_config_path = lifecycle.CONFIG
    compose_command = COMPOSE

    def compose(self, *args, timeout=90, compose_command=None):
        result = subprocess.run((compose_command or self.compose_command) + list(args), capture_output=True, text=True, timeout=timeout)
        assert result.returncode == 0, result.stderr[-4000:]
        return result.stdout.strip()

    def probe(self, code, *options, timeout=90, compose_command=None):
        return self.compose('run', '--rm', '--no-deps', '-T', *options, '--entrypoint', 'python',
            'scout', '-c', textwrap.dedent(code), timeout=timeout, compose_command=compose_command)

    def exec_proxy(self, code):
        return self.compose("exec", "-T", "proxy", "python3", "-c", textwrap.dedent(code))

    def exec_firewall(self, code):
        return self.compose("exec", "-T", "-u", "10000:10000", "firewall", "python3", "-c", textwrap.dedent(code))

    def browser_run(self, **kwargs):
        kwargs.setdefault('compose_files', self.browser_compose_files)
        kwargs.setdefault('config_path', self.browser_config_path)
        return lifecycle.BrowserRun(**kwargs)

    def browser_probe(self, code, *options, timeout=90, run=None):
        if run is None:
            with self.browser_run() as owned:
                return self.browser_probe(code, *options, timeout=timeout, run=owned)
        env = [value for key, setting in run.scout_environment().items() for value in ("-e", key + "=" + setting)]
        return self.probe(code, *env, *options, timeout=timeout, compose_command=run.compose)
