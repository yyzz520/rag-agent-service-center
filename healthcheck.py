import os
import sys
import urllib.request

port = int(os.environ.get("PORT", "8200"))
url = f"http://127.0.0.1:{port}/health"
try:
    with urllib.request.urlopen(url, timeout=3) as resp:
        ok = resp.status == 200
except Exception:
    ok = False
sys.exit(0 if ok else 1)
