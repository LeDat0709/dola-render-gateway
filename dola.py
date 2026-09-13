#!/usr/bin/env python3
"""dola.py - CLI client for a running Dola Render Gateway.

Submits a video generation task to the gateway's OpenAI-compatible API,
polls until it finishes, and downloads the resulting MP4. Pure stdlib.

Usage:
    python dola.py "a cat surfing a wave" --duration 10 --ratio 16:9 -o cat.mp4

Env:
    DOLA_BASE      gateway base URL   (default http://127.0.0.1:8000)
    DOLA_API_KEY   bearer token       (optional; omit in dev mode)

Self-check (no gateway needed, spins up a local stub):
    python dola.py --self-check
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin

DEFAULT_BASE = os.getenv("DOLA_BASE", "http://127.0.0.1:8000")
SUPPORTED_DURATIONS = (10, 15, 30)
SUPPORTED_MODELS = ("seedance-2.0", "seedance-2.5")
DONE_STATES = ("completed", "failed")
POLL_INTERVAL = 5          # seconds between status polls
GENERATE_TIMEOUT = 1800    # ponytail: 30min ceiling covers 30s renders; raise via --timeout


def _req(url, key=None, method="GET", body=None, timeout=60):
    """One HTTP call. Returns parsed JSON for API calls, raw bytes for downloads."""
    headers = {"Accept": "*/*"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        try:
            detail = json.loads(detail).get("detail", detail)
        except (ValueError, AttributeError):
            pass
        raise SystemExit(f"gateway error {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise SystemExit(f"cannot reach gateway at {url}: {e.reason}") from e


def submit(base, key, prompt, model, duration, ratio, size, refs):
    """POST a generation task, return its task id."""
    payload = {"model": model, "prompt": prompt, "duration": duration}
    if ratio:
        payload["ratio"] = ratio
    if size:
        payload["size"] = size
    if refs:
        payload["reference_images"] = list(refs)
    raw = _req(urljoin(base, "/v1/videos/generations"), key, "POST", payload)
    return json.loads(raw)["id"]


def poll(base, key, task_id, interval, timeout, log=print):
    """Poll status until completed/failed or timeout. Returns the final task dict."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        row = json.loads(_req(urljoin(base, f"/v1/videos/{task_id}"), key))
        status = row.get("status")
        if status != last:
            log(f"  {task_id}: {status}", file=sys.stderr)
            last = status
        if status in DONE_STATES:
            return row
        time.sleep(interval)
    raise SystemExit(f"timeout after {timeout}s waiting on {task_id}")


def download(base, video_url, out_path):
    """Fetch the finished MP4 to out_path. Joins relative URLs against base."""
    url = urljoin(base, video_url) if video_url.startswith("/") else video_url
    with open(out_path, "wb") as f:
        f.write(_req(url, timeout=300))
    return out_path


def generate(base, key, prompt, *, model="seedance-2.0", duration=10, ratio=None,
             size=None, refs=(), out=None, interval=POLL_INTERVAL,
             timeout=GENERATE_TIMEOUT, log=print):
    """Full flow: submit -> poll -> download. Returns the output path."""
    task_id = submit(base, key, prompt, model, duration, ratio, size, refs)
    log(f"submitted {task_id}", file=sys.stderr)
    row = poll(base, key, task_id, interval, timeout, log=log)
    if row.get("status") != "completed":
        raise SystemExit(f"generation failed: {row.get('error') or 'unknown error'}")
    out = out or f"{task_id}.mp4"
    download(base, row["video_url"], out)
    log(f"saved {out}", file=sys.stderr)
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description="Generate a video via a Dola Render Gateway.")
    p.add_argument("prompt", nargs="?", help="text prompt")
    p.add_argument("--base", default=DEFAULT_BASE, help=f"gateway URL (default {DEFAULT_BASE})")
    p.add_argument("--key", default=os.getenv("DOLA_API_KEY"), help="bearer token (or DOLA_API_KEY)")
    p.add_argument("--model", default="seedance-2.0", choices=SUPPORTED_MODELS)
    p.add_argument("--duration", type=int, default=10, choices=SUPPORTED_DURATIONS)
    p.add_argument("--ratio", help="e.g. 16:9, 9:16, 1:1")
    p.add_argument("--size", help="e.g. 1280x720 (maps to a ratio on the server)")
    p.add_argument("--ref", action="append", default=[], metavar="URL",
                   help="reference image URL (repeatable)")
    p.add_argument("-o", "--out", help="output file (default <task_id>.mp4)")
    p.add_argument("--interval", type=int, default=POLL_INTERVAL, help="poll seconds")
    p.add_argument("--timeout", type=int, default=GENERATE_TIMEOUT, help="max wait seconds")
    p.add_argument("--self-check", action="store_true", help="run offline self-test and exit")
    args = p.parse_args(argv)

    if args.self_check:
        return _self_check()
    if not args.prompt:
        p.error("prompt is required (or pass --self-check)")

    out = generate(args.base, args.key, args.prompt, model=args.model,
                   duration=args.duration, ratio=args.ratio, size=args.size,
                   refs=args.ref, out=args.out, interval=args.interval,
                   timeout=args.timeout)
    print(out)
    return 0


def _self_check():
    """Spin up a stub gateway and assert submit->poll->download works. Stdlib only."""
    import http.server
    import tempfile
    import threading

    fake_mp4 = b"\x00\x00\x00\x18ftypmp42FAKE-VIDEO-BYTES"
    polls = {"n": 0}

    class Stub(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):  # silence
            pass

        def _send(self, code, body, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self._send(200, json.dumps({"id": "video_test", "status": "queued"}).encode())

        def do_GET(self):
            if self.path == "/videos/out.mp4":
                return self._send(200, fake_mp4, "video/mp4")
            polls["n"] += 1
            status = "completed" if polls["n"] >= 2 else "processing"
            row = {"id": "video_test", "status": status,
                   "video_url": "/videos/out.mp4" if status == "completed" else None}
            self._send(200, json.dumps(row).encode())

    srv = http.server.HTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf:
            out = tf.name
        result = generate(base, None, "test prompt", interval=0, out=out, log=lambda *a, **k: None)
        with open(result, "rb") as f:
            assert f.read() == fake_mp4, "downloaded bytes mismatch"
        assert polls["n"] >= 2, "poll loop did not wait for processing->completed"
        os.unlink(result)
        print("self-check OK")
        return 0
    finally:
        srv.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
