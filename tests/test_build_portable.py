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


def test_download_leftovers_stay_out_of_the_archive(monkeypatch, tmp_path):
    """Служебные папки качалки коллеге не нужны: он откроет архив и увидит сор."""
    module = _load(monkeypatch, tmp_path / "dist")
    _fake_release(tmp_path / "dist", module)
    junk = tmp_path / "dist" / "models" / module.DEFAULT_MODEL / ".cache" / "huggingface"
    junk.mkdir(parents=True)
    (junk / "download.metadata").write_bytes("бухгалтерия качалки".encode())

    module.portable()

    folder = tmp_path / "dist" / f"{module.NAME}-portable"
    assert not (folder / "models" / module.DEFAULT_MODEL / ".cache").exists()
    assert (folder / "models" / module.DEFAULT_MODEL / "encoder.int8.onnx").exists(), \
        "сами веса при этом должны остаться на месте"


def test_stale_portable_copy_does_not_survive_a_rebuild(monkeypatch, tmp_path):
    """Пересобрали exe — прежняя переносная копия становится копией не того файла.

    Пока она лежит в dist, её можно выложить по ошибке: внутри архива всё
    выглядит одинаково, отличается только дата. Так и случилось с 1.1.2.
    """
    out = tmp_path / "dist"
    module = _load(monkeypatch, out)
    _fake_release(out, module)
    module.portable()
    assert (out / f"{module.NAME}-portable.zip").exists()

    module.drop_portable()

    assert not (out / f"{module.NAME}-portable.zip").exists()
    assert not (out / f"{module.NAME}-portable").exists()
    assert (out / f"{module.NAME}.exe").exists(), "сам exe трогать не за что"


def _stop_with(module, monkeypatch, printed: str) -> bool:
    """Прогон stop_running с подставным ответом PowerShell — настоящий убил бы
    работающую у человека программу прямо посреди прогона тестов."""
    import types

    monkeypatch.setattr(module.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(stdout=printed, returncode=0))
    return module.stop_running()


def test_stop_running_says_whether_program_was_up(monkeypatch, tmp_path):
    """От этого ответа зависит, поднимут ли программу обратно после сборки."""
    module = _load(monkeypatch, tmp_path / "dist")
    assert _stop_with(module, monkeypatch, "2\n") is True, "два процесса — программа работала"
    assert _stop_with(module, monkeypatch, "0\n") is False
    assert _stop_with(module, monkeypatch, "\n") is False, "пустой ответ — тоже не работала"


def test_start_again_launches_the_built_exe(monkeypatch, tmp_path):
    """Сборка гасит программу ради перезаписи файла и обязана вернуть её на место."""
    out = tmp_path / "dist"
    module = _load(monkeypatch, out)
    _fake_release(out, module)
    launched = []
    monkeypatch.setattr(module.subprocess, "Popen", lambda args, **k: launched.append(args))

    module.start_again()

    assert launched, "программу не подняли — человек остался без диктовки"
    assert str(out / f"{module.NAME}.exe") in launched[0]


def test_start_again_without_exe_stays_quiet(monkeypatch, tmp_path):
    """Сборка не дошла до exe — поднимать нечего, но и падать второй раз незачем."""
    out = tmp_path / "dist"
    out.mkdir(parents=True)
    module = _load(monkeypatch, out)
    monkeypatch.setattr(module.subprocess, "Popen",
                        lambda *a, **k: pytest.fail("запускать было нечего"))

    module.start_again()


def test_arhiv_modulya_soderzhit_obe_papki(monkeypatch, tmp_path):
    """Модуль казахского — это две папки: распознавание и знаки препинания.
    Без второй текст пойдёт сплошняком, и человек решит, что модуль сломан."""
    import zipfile

    module = _load(monkeypatch, tmp_path)
    for name in (module.MULTILINGUAL_MODEL, module.PUNCT_MODEL):
        folder = tmp_path / "models" / name
        folder.mkdir(parents=True)
        (folder / "веса.onnx").write_bytes(b"0")

    archive = module.module_archive()

    names = zipfile.ZipFile(archive).namelist()
    assert any(module.MULTILINGUAL_MODEL in n for n in names), "нет распознавания"
    assert any(module.PUNCT_MODEL in n for n in names), "нет знаков препинания"
    assert any(n.endswith("Как поставить.txt") for n in names), "нет инструкции"


def test_arhiv_modulya_otkazyvaetsya_bez_znakov_prepinaniya(monkeypatch, tmp_path):
    """Собрать половину модуля хуже, чем не собрать: получатель увидит текст
    без знаков и решит, что программа сломана, а не что архив неполный."""
    module = _load(monkeypatch, tmp_path)
    folder = tmp_path / "models" / module.MULTILINGUAL_MODEL
    folder.mkdir(parents=True)
    (folder / "веса.onnx").write_bytes(b"0")

    with pytest.raises(SystemExit) as beda:
        module.module_archive()

    assert module.PUNCT_MODEL in str(beda.value)


def test_sluzhebnaya_papka_kachalki_ne_popadaet_v_modul(monkeypatch, tmp_path):
    """.cache — бухгалтерия качалки. Чужому человеку в архиве она не нужна."""
    import zipfile

    module = _load(monkeypatch, tmp_path)
    for name in (module.MULTILINGUAL_MODEL, module.PUNCT_MODEL):
        folder = tmp_path / "models" / name
        folder.mkdir(parents=True)
        (folder / "веса.onnx").write_bytes(b"0")
        (folder / ".cache").mkdir()
        (folder / ".cache" / "мусор").write_bytes(b"0")

    names = zipfile.ZipFile(module.module_archive()).namelist()

    assert not any(".cache" in n for n in names)


def _fake_kazakh_weights(out: Path, module) -> None:
    for name in (module.MULTILINGUAL_MODEL, module.PUNCT_MODEL):
        folder = out / "models" / name
        folder.mkdir(parents=True)
        (folder / "веса.onnx").write_bytes(b"0")


def test_kazahskaya_kopiya_soderzhit_vse_chetyre_modeli(monkeypatch, tmp_path):
    """Собранная руками копия однажды уедет без пунктуатора — человек соберёт
    её в спешке и забудет папку. Команда не забывает."""
    module = _load(monkeypatch, tmp_path)
    _fake_release(tmp_path, module)
    _fake_kazakh_weights(tmp_path, module)

    module.portable(extra=(module.MULTILINGUAL_MODEL, module.PUNCT_MODEL),
                    suffix=module.KAZAKH_SUFFIX, env=module.KAZAKH_ENV)

    folder = tmp_path / f"{module.NAME}-portable{module.KAZAKH_SUFFIX}"
    внутри = {p.name for p in (folder / "models").iterdir()}
    assert внутри == {module.DEFAULT_MODEL, module.VAD_MODEL,
                      module.MULTILINGUAL_MODEL, module.PUNCT_MODEL}
    assert (folder / f"{module.NAME}.exe").exists()
    assert "ASR_MODEL=gigaam-multilingual-ctc" in (folder / ".env").read_text(encoding="utf-8")


def test_obychnaya_kopiya_ostayotsya_bez_kazahskogo(monkeypatch, tmp_path):
    """Обычная копия — 222 МБ. Казахские полгигабайта в неё попадать не должны."""
    module = _load(monkeypatch, tmp_path)
    _fake_release(tmp_path, module)
    _fake_kazakh_weights(tmp_path, module)

    module.portable()

    внутри = {p.name for p in (tmp_path / f"{module.NAME}-portable" / "models").iterdir()}
    assert внутри == {module.DEFAULT_MODEL, module.VAD_MODEL}


def test_kazahskaya_kopiya_stiraetsya_pered_novoy_sborkoy(monkeypatch, tmp_path):
    """В ней лежит exe. После пересборки программы она превращается в копию уже
    не того файла — ровно так в выпуск 1.1.2 чуть не уехал архив от 1.1.1."""
    module = _load(monkeypatch, tmp_path)
    stale = tmp_path / f"{module.NAME}-portable{module.KAZAKH_SUFFIX}"
    stale.mkdir(parents=True)
    (stale / "старый.exe").write_bytes(b"0")
    (tmp_path / f"{module.NAME}-portable{module.KAZAKH_SUFFIX}.zip").write_bytes(b"0")

    module.drop_portable()

    assert not stale.exists()
    assert not (tmp_path / f"{module.NAME}-portable{module.KAZAKH_SUFFIX}.zip").exists()


def test_kazahskaya_kopiya_otkazyvaetsya_bez_punktuatora(monkeypatch, tmp_path):
    """Без знаков препинания казахский текст пойдёт сплошняком, и получатель
    решит, что программа сломана, а не что копия неполная."""
    module = _load(monkeypatch, tmp_path)
    _fake_release(tmp_path, module)

    with pytest.raises(SystemExit) as beda:
        module.portable(extra=(module.MULTILINGUAL_MODEL, module.PUNCT_MODEL),
                        suffix=module.KAZAKH_SUFFIX, env=module.KAZAKH_ENV)

    assert module.MULTILINGUAL_MODEL in str(beda.value)
