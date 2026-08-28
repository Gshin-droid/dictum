"""Настройки диктовки в файле .env рядом с программой.

Меню в лотке меняет их на ходу, поэтому нужна запись — а не только чтение, как
у python-dotenv. Правится ровно одна строка: остальные настройки, комментарии и
порядок остаются как были, иначе файл, который человек правил руками, превратился
бы после первого клика в машинный список без пояснений.
"""

from pathlib import Path


def read_all(path: Path) -> dict[str, str]:
    """Все настройки из файла. Нет файла — пустой словарь, это не ошибка."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def write(path: Path, key: str, value: str) -> None:
    """Меняет одну настройку. Нет такой строки — дописывает в конец."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    replaced = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        if stripped.partition("=")[0].strip() == key:
            lines[i] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def as_bool(value: str | None, default: bool = True) -> bool:
    """«1/да/true» — включено, «0/нет/false» — выключено, пусто — как задано умолчанием."""
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "да", "true", "yes", "on"}
