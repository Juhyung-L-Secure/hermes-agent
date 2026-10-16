"""Drive packaged noVNC in an isolated host Chrome, with no provider calls."""

import json
from pathlib import Path
import shutil
import subprocess
import time

from websockets.sync.client import connect


class ViewerClient:
    """Use real HTTP auth, WebSocket/RFB, canvas, mouse and keyboard paths."""

    def __init__(self, directory, access):
        self.profile, self.access = Path(directory), access
        self.child = self.connection = None
        self.sequence = 0
        self.session = None

    def __enter__(self):
        chrome = shutil.which('google-chrome')
        assert chrome, 'Real noVNC verification requires host google-chrome'
        self.profile.mkdir()
        self.child = subprocess.Popen([chrome, '--headless=new', '--no-first-run',
            '--no-default-browser-check', '--disable-background-networking', '--password-store=basic',
            '--disable-dev-shm-usage', '--remote-debugging-port=0', '--window-size=1600,1200',
            '--user-data-dir=' + str(self.profile), 'about:blank'],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 20
            path = self.profile / 'DevToolsActivePort'
            while not path.exists():
                assert self.child.poll() is None and time.monotonic() < deadline, 'Viewer test Chrome startup failed'
                time.sleep(0.05)
            port, endpoint = path.read_text().splitlines()
            self.connection = connect('ws://127.0.0.1:' + port + endpoint, proxy=None, open_timeout=2)
            target = self.command('Target.createTarget', {'url':'about:blank'})['targetId']
            self.session = self.command('Target.attachToTarget', {'targetId':target, 'flatten':True})['sessionId']
            self.command('Fetch.enable', {'handleAuthRequests':True})
            self.command('Page.navigate', {'url':self.access['url']})
            self.evaluate("""(async () => {
                const deadline=Date.now()+15000;
                while (document.readyState!=='complete') {
                    if (Date.now()>deadline) throw new Error('Viewer did not load');
                    await new Promise(resolve=>setTimeout(resolve,50));
                }
                const {default:UI}=await import('/app/ui.js');
                while (!UI.rfb || UI.rfb._rfbConnectionState!=='connected') {
                    if (Date.now()>deadline) throw new Error('VNC did not connect');
                    await new Promise(resolve=>setTimeout(resolve,50));
                }
                return true;
            })()""")
            return self
        except BaseException:
            self.__exit__()
            raise

    def send(self, method, params=None, session=None):
        """Send one CDP request; auxiliary Fetch responses need no await."""
        self.sequence += 1
        request = {'id':self.sequence, 'method':method, 'params':params or {}}
        if session is not None:
            request['sessionId'] = session
        self.connection.send(json.dumps(request))
        return self.sequence

    def command(self, method, params=None):
        """Answer browser HTTP auth and request pauses through real CDP events."""
        expected = self.send(method, params, self.session)
        deadline = time.monotonic() + 25
        while True:
            result = json.loads(self.connection.recv(timeout=max(0.01, deadline-time.monotonic())))
            if result.get('method') == 'Fetch.requestPaused':
                self.send('Fetch.continueRequest', {'requestId':result['params']['requestId']}, result['sessionId'])
            elif result.get('method') == 'Fetch.authRequired':
                self.send('Fetch.continueWithAuth', {'requestId':result['params']['requestId'],
                    'authChallengeResponse':{'response':'ProvideCredentials',
                        'username':self.access['username'], 'password':self.access['password']}}, result['sessionId'])
            elif result.get('id') == expected:
                assert 'error' not in result, 'Viewer test CDP operation failed'
                return result['result']

    def evaluate(self, expression):
        """Inspect only controlled viewer fixture; never print auth-bearing data."""
        result = self.command('Runtime.evaluate', {'expression':expression, 'returnByValue':True, 'awaitPromise':True})
        assert 'exceptionDetails' not in result, 'Viewer fixture script failed'
        return result['result'].get('value')

    def click(self, desktop_x, desktop_y):
        """Click visible noVNC canvas coordinates through host Chrome mouse input."""
        point = self.evaluate(f"""(async () => {{
            const {{default:UI}}=await import('/app/ui.js');
            const deadline=Date.now()+15000;
            while (true) {{
                const canvas=UI.rfb._canvas, r=canvas.getBoundingClientRect();
                const point={{x:r.x+{desktop_x}*r.width/canvas.width,y:r.y+{desktop_y}*r.height/canvas.height}};
                if (document.elementFromPoint(point.x,point.y)===canvas) return point;
                if (Date.now()>deadline) throw new Error('Viewer canvas did not accept input');
                await new Promise(resolve=>requestAnimationFrame(resolve));
            }}
        }})()""")
        self.command('Input.dispatchMouseEvent', {'type':'mouseMoved', **point})
        for kind in ('mousePressed', 'mouseReleased'):
            self.command('Input.dispatchMouseEvent', {'type':kind, 'button':'left',
                'buttons':1 if kind=='mousePressed' else 0, 'clickCount':1, **point})
            if kind == 'mousePressed':
                observed = self.evaluate(f"""(async () => {{
                    const {{default:UI}}=await import('/app/ui.js');
                    return {{mask:UI.rfb._mouseButtonMask,
                        element:document.elementFromPoint({point['x']},{point['y']})?.tagName,
                        focus:document.activeElement.tagName}};
                }})()""")
                assert observed['mask'] == 1, observed

    def type(self, text):
        """Send real key events into focused noVNC canvas, forwarded by RFB."""
        for char in text:
            self.command('Input.dispatchKeyEvent', {'type':'keyDown', 'key':char, 'text':char,
                'code':'Key'+char.upper(), 'windowsVirtualKeyCode':ord(char.upper())})
            self.command('Input.dispatchKeyEvent', {'type':'keyUp', 'key':char, 'code':'Key'+char.upper()})

    def __exit__(self, *_):
        if self.connection is not None:
            self.connection.close()
        if self.child is not None and self.child.poll() is None:
            self.child.terminate()
            try:
                self.child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait()
