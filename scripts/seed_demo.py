"""一键灌样例：登录 → 上传 samples/medical → 轮询到 ready；已存在则跳过。

用法：
  python scripts/seed_demo.py
  python scripts/seed_demo.py --eval      # 再跑一次检索评测
"""
import argparse
import os
import sys
import time
from pathlib import Path

import httpx

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8200")
SAMPLES = Path("samples/medical")
SUPPORTED = {".pdf", ".docx", ".xlsx", ".md", ".txt"}


def sample_files() -> list[Path]:
    return sorted(
        p for p in SAMPLES.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED and not p.name.startswith(("_", "README"))
    )


def login(client: httpx.Client, username: str, password: str) -> str:
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    body = resp.json()
    if resp.status_code != 200 or body.get("code") != 0:
        raise RuntimeError(f"登录失败: {body.get('message')}")
    return body["data"]["token"]


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def existing_docs(client: httpx.Client, token: str) -> dict[str, dict]:
    body = client.get("/api/v1/docs", headers=auth(token)).json()
    return {item["file_name"]: item for item in (body.get("data") or {}).get("items", [])}


def wait_ready(client: httpx.Client, token: str, doc_id: str, timeout: float = 180.0) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/v1/docs/{doc_id}", headers=auth(token)).json()
        data = body.get("data") or {}
        status = data.get("status")
        if status in ("ready", "failed"):
            return status
        time.sleep(1.0)
    return "timeout"


def upload(client: httpx.Client, token: str, path: Path) -> tuple[str, str]:
    with path.open("rb") as fh:
        resp = client.post(
            "/api/v1/docs",
            headers=auth(token),
            files={"file": (path.name, fh, "application/octet-stream")},
            timeout=120.0,
        )
    body = resp.json()
    if resp.status_code != 200 or body.get("code") != 0:
        raise RuntimeError(f"上传失败 {path.name}: {body.get('message')}")
    doc_id = body["data"]["doc_id"]
    return doc_id, wait_ready(client, token, doc_id)


def run_eval(client: httpx.Client) -> dict:
    token = login(client, "admin", "admin123")
    resp = client.post("/api/v1/eval/retrieval", headers=auth(token), json={"note": "seed-demo"}, timeout=600.0)
    body = resp.json()
    if resp.status_code != 200 or body.get("code") != 0:
        raise RuntimeError(f"评测失败: {body.get('message')}")
    return body["data"]["metrics"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--username", default="alice")
    parser.add_argument("--password", default="alice123")
    parser.add_argument("--eval", action="store_true", help="灌完后跑一次检索评测")
    args = parser.parse_args()

    files = sample_files()
    if not files:
        print(f"没有样例文件: {SAMPLES}")
        return 2

    with httpx.Client(base_url=args.base_url, timeout=60.0) as client:
        token = login(client, args.username, args.password)
        docs = existing_docs(client, token)
        uploaded = skipped = failed = 0
        for path in files:
            if path.name in docs:
                skipped += 1
                print(f"skip  {path.name} (already exists, status={docs[path.name].get('status')})")
                continue
            try:
                doc_id, status = upload(client, token, path)
                print(f"{status:8s} {path.name} -> {doc_id}")
                if status == "ready":
                    uploaded += 1
                else:
                    failed += 1
            except Exception as exc:
                failed += 1
                print(f"FAIL   {path.name}: {exc}")

        print(f"summary uploaded={uploaded} skipped={skipped} failed={failed}")
        if args.eval:
            metrics = run_eval(client)
            print("eval:", {k: metrics.get(k) for k in ("total", "recall@1", "recall@5", "mrr", "ndcg@16")})
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
