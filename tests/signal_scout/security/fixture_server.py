"""Disposable listeners/DNS inside existing proxy; no production policy override."""

from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import ssl
import struct
import threading
import time

BASE = Path('/tmp/scout-network-fixture')
COUNTS = Counter()
LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path == '/rebind':
            records = json.loads((BASE / 'records.json').read_text())
            records['rebind.test'] = ['172.30.242.2']
            (BASE / 'records.next').write_text(json.dumps(records))
            (BASE / 'records.next').replace(BASE / 'records.json')
        if self.path.startswith('/redirect/'):
            destination = self.path.removeprefix('/redirect/')
            self.send_response(302)
            self.send_header('Location', destination)
            self.end_headers()
            return
        body = b'<html><body>public fixture survives<script>window.fixtureReady=true</script></body></html>'
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(body)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def get_request(self):
        connection, address = super().get_request()
        local = connection.getsockname()
        with LOCK:
            COUNTS[f'{local[0]}:{local[1]}'] += 1
            (BASE / 'counts.next').write_text(json.dumps(COUNTS))
            (BASE / 'counts.next').replace(BASE / 'counts.json')
        if self.server_port == 443:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(BASE / 'cert.pem', BASE / 'key.pem')
            try:
                connection = context.wrap_socket(connection, server_side=True)
            except OSError:
                connection.close()
                raise
        return connection, address


class Server6(Server):
    address_family = socket.AF_INET6

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        super().server_bind()


def dns():
    """TTL-zero controlled answers; forward unrelated DNS to Docker's resolver."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        server.bind(('127.0.0.53', 53))
        while True:
            packet, peer = server.recvfrom(4096)
            offset, labels = 12, []
            while packet[offset]:
                length = packet[offset]
                labels.append(packet[offset + 1:offset + 1 + length].decode('ascii'))
                offset += length + 1
            offset += 1
            kind = struct.unpack('!H', packet[offset:offset + 2])[0]
            name = '.'.join(labels)
            records = json.loads((BASE / 'records.json').read_text())
            if name not in records:
                reply = packet[:2] + b'\x81\x83' + packet[4:6] + b'\0' * 6 + packet[12:]
            else:
                answers = []
                for address in records[name]:
                    family = socket.AF_INET6 if ':' in address else socket.AF_INET
                    if kind == (28 if family == socket.AF_INET6 else 1):
                        packed = socket.inet_pton(family, address)
                        answers.append(b'\xc0\x0c' + struct.pack('!HHIH', kind, 1, 0, len(packed)) + packed)
                flags = 0x8180 if records[name] else 0x8183
                reply = (packet[:2] + struct.pack('!HHHHH', flags, 1, len(answers), 0, 0)
                         + packet[12:offset + 4] + b''.join(answers))
            server.sendto(reply, peer)


if __name__ == '__main__':
    (BASE / 'counts.json').write_text('{}')
    for cls, address in ((Server, '0.0.0.0'), (Server6, '::')):
        for port in (80, 443, 8080):
            instance = cls((address, port), Handler)
            threading.Thread(target=instance.serve_forever, daemon=True).start()
    threading.Thread(target=dns, daemon=True).start()
    (BASE / 'ready').touch()
    while True:
        time.sleep(30)
