"""基础密钥检查：.env 不能被 git 跟踪；仓库里不能出现常见密钥格式。"""
import re
import subprocess
import sys
from pathlib import Path

PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]
SKIP_PREFIX = ("docs/", "tests/")
SKIP_NAMES = {".env.example"}


def tracked_files() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files"], capture_output=True, text=True, check=True
        ).stdout
        return [line for line in out.splitlines() if line.strip()]
    except Exception as exc:
        print(f"无法读取 git 文件列表: {exc}")
        return []


def scan_text(text: str) -> list[str]:
    hits = []
    for pattern in PATTERNS:
        hits.extend(m.group(0) for m in pattern.finditer(text))
    return hits


def main() -> int:
    files = tracked_files()
    if not files:
        print("secret check SKIPPED: 不是 git 仓库或没有文件")
        return 0
    if ".env" in files:
        print("FAIL: .env 被 git 跟踪")
        return 1

    failures = []
    for name in files:
        if name in SKIP_NAMES or name.startswith(SKIP_PREFIX):
            continue
        path = Path(name)
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        hits = scan_text(text)
        if hits:
            failures.append((name, hits))

    if failures:
        for name, hits in failures:
            print(f"FAIL: {name}: {hits[:3]}")
        return 1
    print(f"secret check PASSED: {len(files)} tracked files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
