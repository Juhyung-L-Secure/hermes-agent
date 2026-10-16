"""Read immutable, image-owned Scout operating settings; never relax protections."""

from pathlib import Path
import ipaddress
import hashlib
import json
import math
import yaml

CONFIG = Path("/opt/scout/config.yaml")
_BROWSER_ACTIONS = frozenset({"click", "navigate", "type", "keypress", "back", "scroll",
    "snapshot", "screenshot", "get_images", "list_tabs", "switch_tab", "close"})
_ACTION_MODES = frozenset({"blocked", "review_required", "allowed_without_review"})


def settings_digest(config):
    """Compare baked operating settings without transporting config contents."""
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def load_settings(path=CONFIG):
    """Validate supplied operating values without echoing config or invalid input."""
    try:
        config = yaml.safe_load(Path(path).read_text())
        logging = config["logging"]
        browser = config["browser"]
        if (browser["mode"] not in {"headless", "headed"} or type(browser["viewer"]) is not bool
                or browser["viewer"] and browser["mode"] != "headed"):
            raise ValueError
        permissions = browser["action_permissions"]
        if (not isinstance(permissions, dict) or set(permissions) - _BROWSER_ACTIONS
                or any(not isinstance(mode, str) or mode not in _ACTION_MODES for mode in permissions.values())
                or type(browser["scroll_pixels"]) is not int or browser["scroll_pixels"] <= 0):
            raise ValueError
        if type(browser["navigation_retries"]) is not int or browser["navigation_retries"] < 0:
            raise ValueError
        viewport = browser["viewport"]
        proxy = config["scout_proxy"]
        lifecycle = config["scout_lifecycle"]
        network = config["scout_network"]
        reviewer = config["scout_reviewer"]
        if set(reviewer) != {"model", "reasoning_effort", "timeout_seconds", "retries"}:
            raise ValueError
        model = reviewer["model"]
        if not isinstance(model, str) or not model or model != model.strip() or model.endswith("-900k"):
            raise ValueError
        if reviewer["reasoning_effort"] not in {"none", "low", "medium", "high", "xhigh", "max"}:
            raise ValueError
        if type(reviewer["timeout_seconds"]) is not int or reviewer["timeout_seconds"] <= 0:
            raise ValueError
        if type(reviewer["retries"]) is not int or reviewer["retries"] < 0:
            raise ValueError
        if set(lifecycle) != {"startup_timeout", "control_connect_timeout", "shutdown_timeout", "smoke_timeout"}:
            raise ValueError
        if set(network) != {"subnet", "proxy_address", "scout_address", "browser_address",
                            "proxy_port", "control_port", "relay_port", "viewer_port",
                            "viewer_subnet", "viewer_address"}:
            raise ValueError
        subnet = ipaddress.IPv4Network(network["subnet"])
        addresses = [ipaddress.IPv4Address(network[key]) for key in
                     ("proxy_address", "scout_address", "browser_address")]
        if (not subnet.is_private or len(set(addresses)) != 3
                or any(address not in subnet or address in {subnet.network_address, subnet.broadcast_address}
                       for address in addresses)):
            raise ValueError
        viewer_subnet = ipaddress.IPv4Network(network["viewer_subnet"])
        viewer_address = ipaddress.IPv4Address(network["viewer_address"])
        if (not viewer_subnet.is_private or viewer_subnet.overlaps(subnet)
                or viewer_address not in viewer_subnet
                or viewer_address in {viewer_subnet.network_address, viewer_subnet.network_address + 1,
                                      viewer_subnet.broadcast_address}):
            raise ValueError
        if len({network[key] for key in ("proxy_port", "control_port", "relay_port", "viewer_port")}) != 4:
            raise ValueError
        if set(logging) != {"level", "max_size_mb", "backup_count"}:
            raise ValueError
        if set(proxy) != {"dns_timeout", "connect_timeout", "idle_timeout"}:
            raise ValueError
        if browser != {"cloud_provider": "local", "engine": "chrome", "use_real_profile": False,
                       "mode": browser["mode"], "viewer": browser["viewer"],
                       "auto_local_for_private_urls": False, "command_timeout": browser["command_timeout"],
                       "navigation_retries": browser["navigation_retries"],
                       "viewport": viewport, "action_permissions": permissions,
                       "scroll_pixels": browser["scroll_pixels"]}:
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
            *[(network[key], 1024, 65535) for key in ("proxy_port", "control_port", "relay_port", "viewer_port")],
        ]:
            if type(value) is not int or not low <= value <= high:
                raise ValueError
        if logging["level"] not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ValueError
        return config
    except (OSError, ValueError, TypeError, KeyError, OverflowError, yaml.YAMLError):
        raise ValueError("Invalid Scout operating settings.") from None
