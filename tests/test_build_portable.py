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
        vad = out / "models" / module.VAD_MODEL
        vad.mkdir(parents=True)
        (vad / "silero_vad.onnx").write_bytes("как будто нарезчик".encode())


def test_portable_folder_has_everything_for_offline_start(monkeypatch, tmp_path):
    module = _load(monkeypatch, tmp_path / "dist")
    _fake_release(tmp_path / "dist", module)

    archive = module.portable()

    folder = tmp_path / "dist" / f"{module.NAME}-portable"
    assert (folder / f"{module.NAME}.exe").exists()
    assert (folder / "models" / module.DEFAULT_MODEL / "encoder.int8.onnx").exists(), \
        "без весов копия перестаёт быть переносной — при запуске полезет в интернет"
    assert (folder / "models" / module.VAD_MODEL).exists(), \
        "без нарезчика расшифровка полезет в интернет — копия перестанет быть переносной"
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


def test_version_file_carries_name_author_and_version(monkeypatch, tmp_path):
    """Паспорт exe: без него файл безымянный и человеку, и антивирусным эвристикам."""
    module = _load(monkeypatch, tmp_path / "dist")
    monkeypatch.setattr(module, "ROOT", ROOT)
    out = tmp_path / "version_info.txt"

    module.make_version_file(out)

    text = out.read_text(encoding="utf-8")
    sys.path.insert(0, str(ROOT))
    from voice_input import APP_AUTHOR, APP_NAME, APP_VERSION

    assert f"StringStruct('ProductName', '{APP_NAME}')" in text
    assert f"StringStruct('FileVersion', '{APP_VERSION}')" in text
    assert APP_AUTHOR in text
    assert "MIT" in text


def test_version_is_three_numbers():
    """Версию читают люди и сравнивают машины — формат должен быть предсказуемым."""
    sys.path.insert(0, str(ROOT))
    from voice_input import APP_VERSION

    parts = APP_VERSION.split(".")
    assert len(parts) == 3, f"ждём вид 1.2.3, а не {APP_VERSION}"
    assert all(part.isdigit() for part in parts), f"в версии не только числа: {APP_VERSION}"
