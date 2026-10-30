import collections
import json
import os
import select
import threading
import time


class Lines:
    def __init__(self, limit):
        self.limit, self.pending, self.discarding = limit, bytearray(), False

    def feed(self, data, final=False):
        for part in data.splitlines(keepends=True):
            ended = part.endswith(b'\n')
            if not self.discarding:
                if len(self.pending) + len(part) > self.limit:
                    self.pending.clear()
                    self.discarding = True
                else:
                    self.pending.extend(part)
            if ended:
                yield None if self.discarding else bytes(self.pending).rstrip(b'\r\n')
                self.pending.clear()
                self.discarding = False
        if final and (self.pending or self.discarding):
            yield None if self.discarding else bytes(self.pending)
            self.pending.clear()
            self.discarding = False


class ProcessOutput:
    def __init__(self, limits):
        self.limit = limits['max_record_bytes']
        self.capacity = limits['max_pending_bytes']
        self.queue, self.size, self.lost = collections.deque(), 0, False
        self.lock = threading.Lock()
        self.deadline = None
        self.fd = 1
        os.set_blocking(self.fd, False)
        self.worker = threading.Thread(target=self._write, name='browser-output', daemon=True)
        self.worker.start()

    def emit(self, component, event, level='INFO', text='', **facts):
        record = {'component': component, 'event': event, 'level': level,
                  'time': time.time(), 'text': text, 'facts': facts}
        payload = (json.dumps(record, ensure_ascii=True) + '\n').encode()
        with self.lock:
            if len(payload) > self.limit or self.size + len(payload) > self.capacity:
                self.lost = True
            else:
                self.queue.append(payload)
                self.size += len(payload)

    def _write(self):
        pending = b''
        while self.deadline is None or time.monotonic() < self.deadline:
            with self.lock:
                if not pending and self.queue:
                    pending = self.queue.popleft()
                done = self.deadline is not None and not pending and not self.queue
                lost, self.lost = self.lost, False
            if done:
                return
            if lost:
                self.emit('launcher', 'diagnostic_loss', 'WARNING', 'Browser diagnostic records dropped.')
            if not pending:
                time.sleep(0.01)
                continue
            try:
                if select.select([], [self.fd], [], 0.05)[1]:
                    count = os.write(self.fd, pending[:4096])
                    pending = pending[count:]
                    with self.lock:
                        self.size -= count
            except (OSError, ValueError):
                return

    def close(self, timeout):
        self.deadline = time.monotonic() + timeout
        self.worker.join(timeout + 0.1)
