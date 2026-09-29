"""Идемпотентная вставка блока v1.1.0. README уже переписан под квалификатор."""
from pathlib import Path

README = Path(__file__).resolve().parents[1] / "README.md"
text = README.read_text(encoding="utf-8")

if "ИЖС-квалификатор лидов" in text:
    print("README уже про квалификатор — пропуск")
else:
    print("В README нет заголовка квалификатора — проверь вручную")
