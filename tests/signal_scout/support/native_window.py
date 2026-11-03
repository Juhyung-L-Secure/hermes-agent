import base64
import json
import shutil
import subprocess


SCRIPT = r'''
$ErrorActionPreference = 'Stop'
Add-Type @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public class ScoutWindow {
 public delegate bool Callback(IntPtr h, IntPtr p);
 [DllImport("user32.dll")] public static extern bool EnumWindows(Callback cb, IntPtr p);
 [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
 [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out Rect r);
 [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, IntPtr p);
 [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
 [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint a, uint b, bool attach);
 [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
 [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint x, uint y, uint d, UIntPtr p);
 [DllImport("user32.dll")] public static extern uint SendInput(uint n, Input[] inputs, int size);
 [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint msg, IntPtr w, IntPtr l);
 public struct Rect { public int Left, Top, Right, Bottom; }
 [StructLayout(LayoutKind.Sequential)] public struct Input { public uint type; public Data data; }
 [StructLayout(LayoutKind.Explicit)] public struct Data {
  [FieldOffset(0)] public Keyboard keyboard;
  [FieldOffset(0)] public Mouse mouse;
 }
 [StructLayout(LayoutKind.Sequential)] public struct Keyboard { public ushort vk, scan; public uint flags, time; public UIntPtr extra; }
 [StructLayout(LayoutKind.Sequential)] public struct Mouse { public int x, y; public uint data, flags, time; public UIntPtr extra; }
 public static void Type(string value) {
  foreach (char c in value) {
   var inputs = new Input[2];
   inputs[0].type = inputs[1].type = 1;
   inputs[0].data.keyboard.scan = inputs[1].data.keyboard.scan = c;
   inputs[0].data.keyboard.flags = 4;
   inputs[1].data.keyboard.flags = 6;
   if (SendInput(2, inputs, Marshal.SizeOf(typeof(Input))) != 2) throw new Exception("Native input refused");
  }
 }
 public static void Focus(IntPtr h) {
  var current = GetCurrentThreadId();
  var foreground = GetWindowThreadProcessId(GetForegroundWindow(), IntPtr.Zero);
  bool attached = current != foreground && AttachThreadInput(current, foreground, true);
  try { SetForegroundWindow(h); } finally { if (attached) AttachThreadInput(current, foreground, false); }
  for (int i=0; i<40 && GetForegroundWindow()!=h; i++) System.Threading.Thread.Sleep(25);
  if (GetForegroundWindow()!=h) throw new Exception("Owned native window did not receive focus");
 }
}
'@
[void][ScoutWindow]::SetProcessDPIAware()
$request = ConvertFrom-Json ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('REQUEST')))
$deadline = [DateTime]::UtcNow.AddSeconds(15)
do {
 $hits = [System.Collections.Generic.List[object]]::new()
 [ScoutWindow]::EnumWindows({param($h,$p)
  if ($request.handle -and $h.ToInt64() -ne $request.handle) { return $true }
  $s = [Text.StringBuilder]::new(1024)
  [void][ScoutWindow]::GetWindowText($h,$s,1024)
  if ($request.handle -or $s.ToString().Contains($request.title)) {
   $r = [ScoutWindow+Rect]::new()
   [void][ScoutWindow]::GetWindowRect($h,[ref]$r)
   $hits.Add([pscustomobject]@{handle=$h;title=$s.ToString();visible=[ScoutWindow]::IsWindowVisible($h);left=$r.Left;top=$r.Top;width=$r.Right-$r.Left;height=$r.Bottom-$r.Top})
  }
  return $true
 },[IntPtr]::Zero) | Out-Null
 if ($hits.Count -eq 1) { break }
 if ($hits.Count -gt 1) { throw 'Ambiguous owned native window' }
 Start-Sleep -Milliseconds 100
} while ([DateTime]::UtcNow -lt $deadline)
if ($hits.Count -ne 1) { throw 'Owned native window not visible' }
$window = $hits[0]
if ($request.action -ne 'inspect') {
 if (-not $window.visible -or -not $window.title.Contains($request.title)) { throw 'Owned native window changed' }
 if ($request.action -eq 'click' -or $request.action -eq 'type') { [ScoutWindow]::Focus($window.handle) }
 if ($request.action -eq 'click') {
  $x = [int]($window.left + $request.x * $window.width / $request.width)
  $y = [int]($window.top + $request.y * $window.height / $request.height)
  if (-not [ScoutWindow]::SetCursorPos($x,$y)) { throw 'Native pointer refused' }
  [ScoutWindow]::mouse_event(2,0,0,0,[UIntPtr]::Zero)
  [ScoutWindow]::mouse_event(4,0,0,0,[UIntPtr]::Zero)
 } elseif ($request.action -eq 'type') {
  [ScoutWindow]::Type($request.text)
 } elseif ($request.action -eq 'close') {
  if (-not [ScoutWindow]::PostMessage($window.handle,16,[IntPtr]::Zero,[IntPtr]::Zero)) { throw 'Native close refused' }
 } else { throw 'Unsupported native test action' }
}
$window.handle = $window.handle.ToInt64()
ConvertTo-Json -InputObject $window -Compress
'''


class NativeWindow:
    def __init__(self, title):
        self.title = title
        self.handle = None

    def _perform(self, action, **args):
        executable = shutil.which('powershell.exe')
        if not executable:
            raise RuntimeError('Native WSLg window verification requires Windows PowerShell interop.')
        payload = base64.b64encode(json.dumps({'title':self.title,'handle':self.handle,'action':action,**args}).encode()).decode()
        script = SCRIPT.replace('REQUEST',payload)
        encoded = base64.b64encode(script.encode('utf-16-le')).decode()
        result = subprocess.run([executable,'-NoProfile','-NonInteractive','-EncodedCommand',encoded],
                                text=True,capture_output=True,timeout=30)
        if result.returncode:
            raise RuntimeError('Native WSLg test interaction failed: '+result.stderr[-1500:])
        observed = json.loads(result.stdout.strip().lstrip('\ufeff'))
        self.handle = observed['handle']
        return observed

    def inspect(self):
        return self._perform('inspect')

    def click(self, x, y, width, height):
        return self._perform('click',x=x,y=y,width=width,height=height)

    def type(self, text):
        return self._perform('type',text=text)

    def close(self):
        return self._perform('close')
