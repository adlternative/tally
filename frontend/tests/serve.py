"""Playwright's isolated backend: the same real server and fake upstream as the HTTP suite."""
import json
import signal
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from test_e2e import Server

server = Server(port=8034)
Path('.test').mkdir(exist_ok=True)
Path('.test/backend.json').write_text(json.dumps({'pageUrl': server.page_url}))
stopped = threading.Event()
for sig in (signal.SIGINT, signal.SIGTERM):
    signal.signal(sig, lambda *_: stopped.set())
try:
    stopped.wait()
finally:
    server.close()
