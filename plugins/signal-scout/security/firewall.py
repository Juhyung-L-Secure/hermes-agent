"""Install namespace policy, subscribe to payload-free NFLOG, then drop privilege."""

import ctypes
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys

from .settings import load_settings
from .runtime_logging import configure_logging, event


def subscribe():
    """Bind NFLOG group 10 while privileged; retain only receive socket after drop."""
    sock = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, 12)
    sock.bind((0, 0))
    sock.settimeout(5)
    sequence = 0
    for family, group, attr, payload in [
        (socket.AF_INET, 0, 1, b"\x03"), (socket.AF_INET6, 0, 1, b"\x03"),
        (socket.AF_UNSPEC, 10, 1, b"\x01"),
        (socket.AF_UNSPEC, 10, 2, struct.pack("!IBB", 0, 1, 0)),
        (socket.AF_UNSPEC, 10, 5, struct.pack("!I", 1)),
    ]:
        sequence += 1
        attribute = struct.pack("HH", len(payload) + 4, attr) + payload
        attribute += b"\x00" * (-len(attribute) % 4)
        body = struct.pack("!BBH", family, 0, group) + attribute
        sock.send(struct.pack("IHHII", len(body) + 16, 0x401, 5, sequence, 0) + body)
        acknowledgement = sock.recv(65536)
        if len(acknowledgement) < 20 or struct.unpack_from("i", acknowledgement, 16)[0] != 0:
            raise RuntimeError("Firewall event subscription failed.")
    sock.settimeout(None)
    return sock


def install_rules(network):
    """Permit browser proxy and owned CDP control; log denials without payload."""
    for binary in ("iptables", "ip6tables"):
        rules = [["-P", chain, "DROP"] for chain in ("INPUT", "FORWARD", "OUTPUT")]
        rules += [["-F", chain] for chain in ("INPUT", "FORWARD", "OUTPUT")]
        if binary == "iptables":
            rules += [
                ["-A", "OUTPUT", "-d", network["proxy_address"], "-p", "tcp", "--dport", str(network["proxy_port"]), "-j", "ACCEPT"],
                ["-A", "INPUT", "-s", network["proxy_address"], "-p", "tcp", "--sport", str(network["proxy_port"]), "-m", "conntrack",
                 "--ctstate", "ESTABLISHED", "-j", "ACCEPT"],
            ]
            forward = ["-s", network["scout_address"], "-d", network["browser_address"],
                       "-p", "tcp", "--dport", str(network["relay_port"])]
            reply = ["-s", network["browser_address"], "-d", network["scout_address"],
                     "-p", "tcp", "--sport", str(network["relay_port"]), "-m", "conntrack", "--ctstate", "ESTABLISHED"]
            rules += [["-A", "INPUT", *forward, "-j", "ACCEPT"],
                      ["-A", "OUTPUT", *reply, "-j", "ACCEPT"]]
            for chain, interface in (("OUTPUT", "-o"), ("INPUT", "-i")):
                local = ["-A", chain, interface, "lo", "-s", "127.0.0.1", "-d", "127.0.0.1", "-p", "tcp"]
                rules += [local + ["--dport", str(network["control_port"]), "-j", "ACCEPT"],
                          local + ["--sport", str(network["control_port"]), "-m", "conntrack", "--ctstate", "ESTABLISHED", "-j", "ACCEPT"]]
        for chain in ("INPUT", "FORWARD", "OUTPUT"):
            rules += [["-A", chain, "-j", "NFLOG", "--nflog-group", "10", "--nflog-size", "0"],
                      ["-A", chain, "-j", "REJECT"]]
        for rule in rules:
            subprocess.run([binary, *rule], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def drop_privileges():
    """Discard all bounding/effective capabilities and switch to Scout UID/GID."""
    libc = ctypes.CDLL(None, use_errno=True)
    for capability in range(64):
        if libc.prctl(24, capability, 0, 0, 0) != 0 and ctypes.get_errno() != 22:
            raise RuntimeError("Firewall privilege drop failed.")
    os.setgroups([])
    os.setgid(10000)
    os.setuid(10000)
    state = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines())
    if any(int(state[key].strip(), 16) for key in ("CapEff", "CapBnd", "CapPrm", "CapAmb")):
        raise RuntimeError("Firewall privilege drop failed.")


def main():
    """Start enforcement and best-effort rejection logging in existing helper."""
    os.umask(0o077)
    config = load_settings()
    if len(sys.argv) != 1:
        raise ValueError("Invalid firewall command.")
    sock = subscribe()
    install_rules(config["scout_network"])
    drop_privileges()
    configure_logging("firewall", config)
    Path("/tmp/firewall-ready").touch()
    event("firewall", "started")
    while True:
        try:
            packet = sock.recv(65536)
        except OSError:
            # Kernel event loss never changes the installed firewall rules.
            from .runtime_logging import diagnostic
            diagnostic()
            continue
        offset = 0
        while offset + 20 <= len(packet):
            length, kind = struct.unpack_from("IH", packet, offset)
            if length < 20 or offset + length > len(packet):
                break
            if kind == 0x400 and packet[offset + 16] in (socket.AF_INET, socket.AF_INET6):
                event("firewall", "denied_ipv4" if packet[offset + 16] == socket.AF_INET else "denied_ipv6", "WARNING")
            offset += (length + 3) & ~3


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("scout firewall startup failed", file=sys.stderr)
        raise SystemExit(1) from None
