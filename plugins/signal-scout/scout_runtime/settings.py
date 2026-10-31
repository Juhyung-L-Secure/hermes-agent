from pathlib import Path
import ipaddress
import hashlib
import json
import math
import re
import stat
import yaml

CONFIG = Path("/opt/scout/config.yaml")
from .actions import ACTION_FIELDS, ACTION_MODES


def settings_digest(config):
    """Compare complete operating settings without transporting config contents."""
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def load_settings(path=None):
    """Require a read-only runtime mount, or validate an explicit host source."""
    try:
        if path is None:
            mounts = []
            for line in Path('/proc/self/mountinfo').read_text().splitlines():
                fields = line.split()
                target = re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), fields[4])
                if target == str(CONFIG):
                    mounts.append(fields[5].split(','))
            if (len(mounts) != 1 or 'ro' not in mounts[0]
                    or CONFIG.is_symlink() or not stat.S_ISREG(CONFIG.stat().st_mode)):
                raise ValueError
            path = CONFIG
        if not stat.S_ISREG(Path(path).stat().st_mode):
            raise ValueError
        return _parse_settings(Path(path).read_bytes())
    except (OSError, ValueError):
        raise ValueError('Invalid Scout operating settings.') from None


def _parse_settings(contents):
    try:
        config = yaml.safe_load(contents)
        logging = config["logging"]
        diagnostics = config["scout_diagnostics"]
        if (set(diagnostics) != {"max_record_bytes", "max_pending_bytes"}
                or type(diagnostics["max_record_bytes"]) is not int
                or not 1024 <= diagnostics["max_record_bytes"] <= 1024 ** 2
                or type(diagnostics["max_pending_bytes"]) is not int
                or not 2 * diagnostics["max_record_bytes"] <= diagnostics["max_pending_bytes"] <= 16 * 1024 ** 2):
            raise ValueError
        browser = config["browser"]
        if browser["mode"] not in {"headless", "headed"}:
            raise ValueError
        permissions = browser["action_permissions"]
        if (not isinstance(permissions, dict) or set(permissions) - ACTION_FIELDS.keys()
                or any(not isinstance(mode, str) or mode not in ACTION_MODES for mode in permissions.values())
                or type(browser["scroll_pixels"]) is not int or browser["scroll_pixels"] <= 0):
            raise ValueError
        if type(browser["navigation_retries"]) is not int or browser["navigation_retries"] < 0:
            raise ValueError
        viewport = browser["viewport"]
        proxy = config["scout_proxy"]
        lifecycle = config["scout_lifecycle"]
        network = config["scout_network"]
        if set(lifecycle) != {"startup_timeout", "control_connect_timeout", "shutdown_timeout", "smoke_timeout"}:
            raise ValueError
        if set(network) != {"subnet", "proxy_address", "scout_address", "browser_address",
                            "proxy_port", "control_port", "relay_port"}:
            raise ValueError
        subnet = ipaddress.IPv4Network(network["subnet"])
        addresses = [ipaddress.IPv4Address(network[key]) for key in
                     ("proxy_address", "scout_address", "browser_address")]
        if (not subnet.is_private or len(set(addresses)) != 3
                or any(address not in subnet or address in {subnet.network_address, subnet.broadcast_address}
                       for address in addresses)):
            raise ValueError
        if len({network[key] for key in ("proxy_port", "control_port", "relay_port")}) != 3:
            raise ValueError
        if set(logging) != {"level", "max_size_mb", "backup_count"}:
            raise ValueError
        if set(proxy) != {"dns_timeout", "connect_timeout", "idle_timeout"}:
            raise ValueError
        if browser != {"cloud_provider": "local", "engine": "chrome", "use_real_profile": False,
                       "mode": browser["mode"],
                       "auto_local_for_private_urls": False, "command_timeout": browser["command_timeout"],
                       "navigation_retries": browser["navigation_retries"],
                       "observation_timeout": browser["observation_timeout"],
                       "interaction_observation_timeout": browser["interaction_observation_timeout"],
                       "viewport": viewport, "action_permissions": permissions,
                       "scroll_pixels": browser["scroll_pixels"]}:
            raise ValueError
        for key in ("observation_timeout", "interaction_observation_timeout"):
            value = browser[key]
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 120:
                raise ValueError
        if browser["interaction_observation_timeout"] > browser["observation_timeout"]:
            raise ValueError
        if set(viewport) != {"width", "height", "device_scale_factor"}:
            raise ValueError
        for key in ("width", "height"):
            if type(viewport[key]) is not int or not 1 <= viewport[key] <= 2**31 - 1:
                raise ValueError
        scale = viewport["device_scale_factor"]
        if type(scale) not in (int, float) or not math.isfinite(scale) or scale <= 0:
            raise ValueError
        if browser["use_real_profile"] is not False or browser["auto_local_for_private_urls"] is not False:
            raise ValueError
        for value, low, high in [
            (logging["max_size_mb"], 1, 1024), (logging["backup_count"], 1, 20),
            (browser["command_timeout"], 1, 120),
            *[(proxy[key], 1, 300) for key in proxy],
            *[(lifecycle[key], 1, 300) for key in lifecycle],
            *[(network[key], 1024, 65535) for key in ("proxy_port", "control_port", "relay_port")],
        ]:
            if type(value) is not int or not low <= value <= high:
                raise ValueError
        if logging["level"] not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ValueError
        return config
    except (OSError, ValueError, TypeError, KeyError, OverflowError, yaml.YAMLError):
        raise ValueError("Invalid Scout operating settings.") from None
