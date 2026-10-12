"""Disposable listeners/DNS inside existing proxy; no production policy override."""

from collections import Counter
import base64
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
        content_type = 'text/html'
        body = b'<html><body>public fixture survives<script>window.fixtureReady=true</script></body></html>'
        if self.path.startswith('/attachment/'):
            body = b'download fixture must never be saved'
            content_type = 'application/octet-stream'
        elif self.path == '/media/image.png':
            body = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
            content_type = 'image/png'
        elif self.path == '/media/audio.wav':
            samples = b'\0' * 800
            body = (b'RIFF' + struct.pack('<I', 36 + len(samples)) + b'WAVEfmt '
                    + struct.pack('<IHHIIHH', 16, 1, 1, 8000, 16000, 2, 16)
                    + b'data' + struct.pack('<I', len(samples)) + samples)
            content_type = 'audio/wav'
        elif self.path.startswith('/safeguards'):
            body = b'''<html><body style="height:2000px">download and media fixture
              <img id="image" src="/media/image.png">
              <audio id="audio" src="/media/audio.wav" preload="auto"></audio>
              <script>window.fixtureReady=true</script></body></html>'''
        elif self.path.startswith('/evidence-child'):
            body = b'''<html><body><button id="child" data-clicked="false" onclick="window.clicked=true;this.dataset.clicked='true'">Frame button</button>
              <input id="childinput" aria-label="Frame input"><script>window.clicked=false;window.keys=[];
                document.querySelector('#childinput').addEventListener('keydown',e=>{
                  window.keys.push(e.key);e.target.dataset.keys=JSON.stringify(window.keys);
                });
              </script></body></html>'''
        elif self.path.startswith('/gate'):
            body = b'''<html><head><title>Gate fixture</title></head><body>
              <h1>Guarded research controls</h1>
              <button id="click" onclick="window.clicks++;document.querySelector('#state').textContent='Clicked '+window.clicks">Controlled click</button>
              <output id="state">Untouched</output>
              <form id="form"><input id="query" aria-label="Gate query" value="initial">
                <input id="other" aria-label="Other query" value="other"><button>Submit query</button></form>
              <button id="popup" onclick="window.open('http://public.test/gate-popup','gate-popup','popup,width=540,height=360')">Open popup</button>
              <a id="download" href="/attachment/gate-fixture.bin">Download fixture</a>
              <img id="image" src="/media/image.png" alt="Fixture image">
              <audio id="audio" src="/media/audio.wav" preload="auto" loop></audio>
              <button id="play" onclick="document.querySelector('#audio').play()">Play audio</button>
              <iframe id="same" src="/evidence-child" style="width:500px;height:120px"></iframe>
              <iframe id="cross" src="http://v6.test/evidence-child" style="width:500px;height:120px"></iframe>
              <div style="height:1800px"></div><button>Below gate fold</button>
              <script>
                window.clicks=0;window.submits=0;window.keys=[];window.fixtureReady=false;
                document.addEventListener('keydown',e=>window.keys.push(e.key));
                document.querySelector('#form').addEventListener('submit',e=>{e.preventDefault();window.submits++});
                addEventListener('load',()=>window.fixtureReady=true);
              </script></body></html>'''
        elif self.path.startswith('/evidence-focus'):
            body = b'''<html><body><div id="focus-host"></div>
              <input id="combo" role="combobox" aria-label="Research choice" value="unchanged"
                aria-controls="choices" aria-expanded="true" aria-activedescendant="choice-a">
              <div id="choices" role="listbox"><div id="choice-a" role="option">Research</div>
                <div id="choice-b" role="option">Purchase</div>
                <div id="choice-c" role="option" aria-labelledby="external-label"
                  aria-describedby="external-description"></div></div>
              <span id="external-label">Research</span><span id="external-description">Research details</span>
              <script>
                const root=document.querySelector('#focus-host').attachShadow({mode:'closed'});
                root.innerHTML='<input aria-label="Closed first" value="same"><input aria-label="Closed second" value="same">';
                window.shadowInputs=Array.from(root.querySelectorAll('input'));
                window.keys=0;document.addEventListener('keydown',()=>window.keys++);
                window.fixtureReady=true;
              </script></body></html>'''
        elif self.path.startswith('/evidence'):
            body = b'''<html><head><title>Evidence fixture</title></head><body>
              <h1>Research controls</h1><a id="link" href="/safe">Open research</a>
              <form id="form" action="/search" method="get">
                <input id="query" name="q" aria-label="Query" value="initial">
                <input id="password" name="secret" type="password" aria-label="Fixture password" value="fixture-secret">
                <button id="submit">Search</button>
              </form>
              <iframe id="same" src="/evidence-child" style="width:500px;height:120px"></iframe>
              <iframe id="cross" src="http://v6.test/evidence-child" style="width:500px;height:120px"></iframe>
              <div style="height:1800px"></div>
              <button id="low">Below fold</button>
              <button id="large" style="width:1600px;height:1300px">Oversized</button>
              <script>
                window.counts={click:0,input:0,key:0,submit:0,focus:0};
                for(const [event,key] of Object.entries({click:'click',input:'input',keydown:'key',submit:'submit',focusin:'focus'}))
                  document.addEventListener(event,e=>{window.counts[key]++;if(event==='submit')e.preventDefault()});
                window.fixtureReady=false;
                addEventListener('load',()=>window.fixtureReady=true);
              </script></body></html>'''
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Content-Type', content_type)
        if self.path.startswith('/attachment/'):
            self.send_header('Content-Disposition', 'attachment; filename="' + self.path.rsplit('/', 1)[1] + '"')
        if self.path.startswith('/attachment/fixture-control-'):
            self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        if self.path.startswith('/attachment/fixture-control-'):
            # Native-policy positive control: headers start a real download,
            # then native UI cancellation occurs before any payload is sent.
            time.sleep(15)
            return
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
