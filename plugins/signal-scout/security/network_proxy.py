"""Public HTTP:80/CONNECT:443 proxy with one checked, numeric connection target.

Resolve each connection afresh, reject an entire mixed public/private answer,
connect to a validated numeric address, then verify the peer before relaying.
TLS remains an opaque end-to-end tunnel. No destination, header, or body logs.
"""

import asyncio
import ipaddress
import re
import socket
import struct

from .settings import load_settings
from .urls import validate_url
from .runtime_logging import configure_logging, event


class Denied(Exception):
    """Carry only a fixed, non-sensitive rejection reason."""


def public_address(value):
    """Classify both IP families, including transition ranges and cloud platform IPs."""
    address = ipaddress.ip_address(value)
    if not address.is_global or address.is_multicast or address.is_reserved:
        return False
    if address.version == 4:
        return (address != ipaddress.ip_address("168.63.129.16")
                and address not in ipaddress.ip_network("192.88.99.0/24"))
    return (address in ipaddress.ip_network("2000::/3")
            and address not in ipaddress.ip_network("2002::/16")
            and address not in ipaddress.ip_network("2001::/23")
            and address not in ipaddress.ip_network("3ffe::/16")
            and address not in ipaddress.ip_network("3fff::/20"))


async def connect_public(host, port, settings):
    """Resolve once, validate every answer, pin connect, and verify actual peer."""
    if port not in {80, 443}:
        raise Denied("destination_port")
    loop = asyncio.get_running_loop()
    try:
        answers = await asyncio.wait_for(loop.getaddrinfo(host, port, type=socket.SOCK_STREAM),
                                         settings["dns_timeout"])
    except (OSError, TimeoutError, UnicodeError):
        raise Denied("dns_failed") from None
    if not answers or any(not public_address(answer[4][0]) for answer in answers):
        raise Denied("private_destination")
    # One selected address, one attempt. Never re-resolve between check and connect.
    family, kind, protocol, _, target = answers[0]
    connection = socket.socket(family, kind, protocol)
    connection.setblocking(False)
    try:
        await asyncio.wait_for(loop.sock_connect(connection, target), settings["connect_timeout"])
        peer = connection.getpeername()
        if not public_address(peer[0]) or (peer[0], peer[1]) != (target[0], port):
            raise Denied("peer_mismatch")
        return await asyncio.open_connection(sock=connection)
    except BaseException:
        connection.close()
        raise


def parse_request(header):
    """Parse one bounded proxy request and remove proxy-only headers before relay."""
    try:
        lines = header.decode("ascii").split("\r\n")
        method, target, version = lines[0].split(" ")
        if version not in {"HTTP/1.0", "HTTP/1.1"} or not re.fullmatch("[A-Z]+", method):
            raise ValueError
        parts = validate_url("https://" + target if method == "CONNECT" else target)
        if method == "CONNECT":
            if parts.path or parts.query or parts.fragment or parts.port != 443:
                raise Denied("destination_port")
        elif parts.scheme != "http" or (parts.port or 80) != 80:
            raise Denied("destination_port")
        fields = []
        for line in lines[1:-2]:
            name, value = line.split(":", 1)
            if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name) or any(
                    ord(char) < 32 and char != "\t" for char in value):
                raise ValueError
            fields.append((name.lower(), value.strip()))
        if sum(name == "host" for name, _ in fields) != 1:
            raise ValueError
        if sum(name == "content-length" for name, _ in fields) > 1 or (
                any(name == "content-length" for name, _ in fields)
                and any(name == "transfer-encoding" for name, _ in fields)):
            raise ValueError
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        forwarded = [f"{method} {path} {version}", f"Host: {parts.netloc}"]
        forwarded.extend(f"{name}: {value}" for name, value in fields if name not in {
            "host", "connection", "proxy-connection", "proxy-authorization",
        })
        upgrade = any(name == "upgrade" and value.lower() == "websocket" for name, value in fields)
        forwarded += ["Connection: Upgrade" if upgrade else "Connection: close", "", ""]
        return method, parts.hostname, 443 if method == "CONNECT" else 80, "\r\n".join(forwarded).encode()
    except (ValueError, UnicodeError):
        raise Denied("invalid_request") from None


async def relay(reader, writer, timeout):
    """Forward bounded chunks until EOF or configured inactivity timeout."""
    while data := await asyncio.wait_for(reader.read(65536), timeout):
        writer.write(data)
        await asyncio.wait_for(writer.drain(), timeout)


async def serve_client(reader, writer, settings, network):
    """Apply policy to every new HTTP request/tunnel; reset only denied connection."""
    upstream = None
    tasks = []
    try:
        if writer.get_extra_info("peername")[0] not in {network["scout_address"], network["browser_address"]}:
            raise Denied("client_denied")
        header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), settings["idle_timeout"])
        method, host, port, outgoing = parse_request(header)
        remote, upstream = await connect_public(host, port, settings)
        if method == "CONNECT":
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
        else:
            upstream.write(outgoing)
            await upstream.drain()
        event("proxy", "connection_allowed", "DEBUG")
        tasks = [asyncio.create_task(relay(reader, upstream, settings["idle_timeout"])),
                 asyncio.create_task(relay(remote, writer, settings["idle_timeout"]))]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except Denied as exc:
        event("proxy", str(exc), "WARNING")
        # A 403 document would make a forbidden main navigation appear successful.
        writer.get_extra_info("socket").setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                                                  struct.pack("ii", 1, 0))
    except (OSError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        event("proxy", "connection_failed", "WARNING")
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if upstream is not None:
            upstream.close()
        writer.close()


async def main():
    """Run only existing proxy service; invalid configuration prevents startup."""
    config = load_settings()
    configure_logging("proxy", config)
    network = config["scout_network"]
    server = await asyncio.start_server(lambda r, w: serve_client(r, w, config["scout_proxy"], network),
                                        network["proxy_address"], network["proxy_port"], limit=65536)
    from pathlib import Path
    Path("/tmp/proxy-ready").touch()
    event("proxy", "started")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        import sys
        print("scout proxy startup failed", file=sys.stderr)
        raise SystemExit(1) from None
