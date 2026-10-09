"""Read immutable, image-owned Scout operating settings; never relax protections."""

from pathlib import Path
import ipaddress
import hashlib
import json
import yaml

CONFIG = Path("/opt/scout/config.yaml")


def settings_digest(config):
    """Compare baked operating settings without transporting config contents."""
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def load_settings(path=CONFIG):
    """Validate supplied operating values without echoing config or invalid input."""
    try:
        config = yaml.safe_load(Path(path).read_text())
        logging = config["logging"]
        browser = config["browser"]
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
                       "auto_local_for_private_urls": False, "command_timeout": browser["command_timeout"]}:
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
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError):
        raise ValueError("Invalid Scout operating settings.") from None
