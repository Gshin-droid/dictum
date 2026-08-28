"""Проверка выпуска: разбор ответа VirusTotal и выбор способа загрузки."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("release_check", ROOT / "release_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CLEAN = {
    "stats": {"malicious": 0, "suspicious": 0, "undetected": 61, "harmless": 0,
              "timeout": 0, "type-unsupported": 11},
    "results": {"Kaspersky": {"category": "undetected", "result": None}},
}
DIRTY = {
    "stats": {"malicious": 2, "suspicious": 1, "undetected": 58, "harmless": 0,
              "timeout": 0, "type-unsupported": 11},
    "results": {
        "Kaspersky": {"category": "undetected", "result": None},
        "Bkav": {"category": "malicious", "result": "W32.AIDetectMalware"},
        "Zillya": {"category": "suspicious", "result": None},
    },
}


def test_clean_verdict_says_so_plainly():
    module = _load()
    text = module.verdict(CLEAN["stats"], CLEAN["results"], "abc123")
    assert "Чисто" in text
    assert "ругаются: 0" in text
    assert "abc123" in text, "без ссылки на отчёт вывод бесполезен"


def test_dirty_verdict_names_every_engine():
    """Числа мало: надо видеть, кто именно ругается — от этого зависит, тревожиться ли."""
    module = _load()
    text = module.verdict(DIRTY["stats"], DIRTY["results"], "abc123")
    assert "ругаются: 2" in text or "ругаются: 3" in text
    assert "Bkav: W32.AIDetectMalware" in text
    assert "Zillya: suspicious" in text, "движок без названия находки тоже должен попасть в список"
    assert "Kaspersky" not in text.split("Отчёт")[0].split("\n\n")[1:], "чистый движок в списке лишний"


def test_unsupported_engines_are_not_counted_as_checked():
    """Движки, которые не умеют такой формат, ничего не проверили — в счёт не идут."""
    module = _load()
    text = module.verdict(CLEAN["stats"], CLEAN["results"], "abc123")
    assert "Проверено движков: 61" in text


def test_big_file_goes_through_special_url(tmp_path, monkeypatch):
    """Больше 32 МБ обычной загрузкой не проходит — нужен отдельный адрес."""
    module = _load()
    big = tmp_path / "big.exe"
    big.write_bytes(b"x" * (module.DIRECT_UPLOAD_LIMIT + 1))
    asked = []

    def fake_request(url, key, data=None, content_type=None):
        asked.append(url)
        if url.endswith("/files/upload_url"):
            return {"data": "https://upload.example/большой"}
        return {"data": {"id": "номер-проверки"}}

    monkeypatch.setattr(module, "request", fake_request)
    assert module.upload(big, "ключ") == "номер-проверки"
    assert asked[0].endswith("/files/upload_url")
    assert asked[1] == "https://upload.example/большой"


def test_small_file_goes_straight(tmp_path, monkeypatch):
    module = _load()
    small = tmp_path / "small.exe"
    small.write_bytes(b"x" * 100)
    asked = []

    monkeypatch.setattr(module, "request", lambda url, key, data=None, content_type=None: (
        asked.append(url), {"data": {"id": "номер"}})[1])
    module.upload(small, "ключ")
    assert asked == [f"{module.API}/files"]


def test_multipart_carries_the_file_whole():
    module = _load()
    payload = b"\x00\x01" + "двоичное".encode("utf-8")
    body, content_type = module.multipart("file", "dictum.exe", payload)
    assert "boundary=" in content_type
    assert b'name="file"; filename="dictum.exe"' in body
    assert payload in body, "файл должен уехать байт в байт"


def test_key_is_read_from_environment(monkeypatch):
    module = _load()
    monkeypatch.setenv("VT_API_KEY", "  ключ-с-пробелами  ")
    assert module.api_key() == "ключ-с-пробелами"


def test_missing_key_stops_with_instructions(monkeypatch):
    """Без ключа скрипт должен объяснить, как его завести, а не упасть трассировкой."""
    module = _load()
    monkeypatch.delenv("VT_API_KEY", raising=False)
    monkeypatch.setitem(sys.modules, "winreg", None)  # как будто реестра нет
    with pytest.raises(SystemExit) as stop:
        module.api_key()
    assert "SetEnvironmentVariable" in str(stop.value)
