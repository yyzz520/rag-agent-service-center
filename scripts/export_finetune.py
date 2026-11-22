"""导出通过评测的微调样本：make export-finetune。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rag.finetune_export import export_latest  # noqa: E402

if __name__ == "__main__":
    print(export_latest())
