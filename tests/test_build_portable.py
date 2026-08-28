"""Переносная сборка: в папке обязана лежать модель, иначе она не переносная."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(monkeypatch, out: Path):
    monkeypatch.syspath_prepend(str(ROOT))
    monkeypatch.setitem(sys.modules, "keyboard", type(sys)("keyboard"))  # build_exe тянет voice_input
    spec = importlib.util.spec_from_file_location("build_exe", ROOT / "build_exe.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "OUT", out)
    return module


def _fake_release(out: Path, module, with_weights: bool = True) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{module.NAME}.exe").write_bytes("как будто exe".encode())
    if with_weights:
        weights = out / "models" / module.DEFAULT_MODEL
        weights.mkdir(parents=True)
        (weights / "encoder.int8.onnx").write_bytes("как будто веса".encode())


def test_portable_folder_has_everything_for_offline_start(monkeypatch, tmp_path):
    module = _load(monkeypatch, tmp_path / "dist")
    _fake_release(tmp_path / "dist", module)

    archive = module.portable()

    folder = tmp_path / "dist" / f"{module.NAME}-portable"
    assert (folder / f"{module.NAME}.exe").exists()
    assert (folder / "models" / module.DEFAULT_MODEL / "encoder.int8.onnx").exists(), \
        "без весов копия перестаёт быть переносной — при запуске полезет в интернет"
    assert (folder / "Прочти меня.txt").exists(), "человеку нужна инструкция в самой папке"
    assert archive.exists() and archive.suffix == ".zip"


def test_portable_refuses_without_weights(monkeypatch, tmp_path):
    """Молча собрать копию без модели нельзя: поломка вскроется у чужого человека."""
    module = _load(monkeypatch, tmp_path / "dist")
    _fake_release(tmp_path / "dist", module, with_weights=False)

    with pytest.raises(SystemExit) as stop:
        module.portable()
    assert "скачает" in str(stop.value)


def test_readme_names_the_key_and_the_tray(monkeypatch, tmp_path):
    """Инструкция в папке — единственное, что человек прочтёт. Главное в ней быть обязано."""
    module = _load(monkeypatch, tmp_path / "dist")
    text = module.READ_ME_FIRST
    for must in ("F8", "Esc", "рядом с часами", "Program Files", "dictum.exe"):
        assert must in text, f"в инструкции нет про «{must}»"
