"""Голосовой ввод: хоткей, отмена записи, громкость для волны, метки времени в логе."""

import importlib.util
import sys
import threading
import time
import types
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(monkeypatch, fake_keyboard):
    # при обычном запуске tools/ лежит на пути сам (скрипт запускают оттуда),
    # а мы грузим модуль по файлу — иначе его «import voice_settings» не найдётся
    monkeypatch.syspath_prepend(str(ROOT))
    monkeypatch.setitem(sys.modules, "keyboard", fake_keyboard)
    spec = importlib.util.spec_from_file_location("voice_input", ROOT / "voice_input.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.winsound, "Beep", lambda *a, **kw: None)
    return module


def _fake(on_press_ok: bool = True):
    calls = []

    def on_press_key(key, callback):
        if not on_press_ok:
            raise ValueError("это сочетание, а не клавиша")
        calls.append(("on_press_key", key))

    fake = types.SimpleNamespace(
        on_press_key=on_press_key,
        add_hotkey=lambda key, callback: calls.append(("add_hotkey", key)),
        unhook=lambda handler: calls.append(("unhook", handler)),
    )
    return fake, calls


def _idle_recorder(module):
    """Recorder без загрузки Whisper — нужны только поля, которые читает управление."""
    rec = module.Recorder.__new__(module.Recorder)
    rec.lock = threading.Lock()
    rec.busy = False
    rec.recording = False
    rec.last_text = ""
    rec.gain = 2.2
    rec.frames = []
    rec.levels = module.deque(maxlen=module.LEVELS_KEPT)
    rec._notice = ("", 0.0)
    rec._esc_hook = None
    rec._timer = None
    rec.stream = None
    rec.switching = False
    rec.save_samples = True
    rec.asr_model = module.DEFAULT_ASR_MODEL
    rec.model_name = module.DEFAULT_ASR_MODEL
    rec._recognize = lambda audio: "распознано"
    rec.language = "ru"
    return rec


# --- горячая клавиша -------------------------------------------------------


def test_single_key_uses_key_hook(monkeypatch):
    fake, calls = _fake(on_press_ok=True)
    _load(monkeypatch, fake).bind_hotkey("f8", lambda: None)
    assert calls == [("on_press_key", "f8")]


def test_combo_falls_back_to_add_hotkey(monkeypatch):
    fake, calls = _fake(on_press_ok=False)
    _load(monkeypatch, fake).bind_hotkey("ctrl+alt+d", lambda: None)
    assert calls == [("add_hotkey", "ctrl+alt+d")]


# --- окно не должно ждать драйвер -----------------------------------------


def test_toggle_never_blocks_the_window(monkeypatch):
    """Залипший звуковой драйвер не должен задерживать поток окна — иначе оно висит."""
    module = _load(monkeypatch, _fake()[0])
    rec = _idle_recorder(module)
    entered = threading.Event()
    release = threading.Event()
    rec._start = lambda: (entered.set(), release.wait(5))

    started_at = time.monotonic()
    rec.toggle()
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.5, f"toggle задержал вызывающий поток на {elapsed:.1f} с"
    assert entered.wait(2), "запись так и не началась в рабочем потоке"
    rec.toggle()  # повторное нажатие, пока драйвер занят, — просто игнорируется
    release.set()


# --- отмена по Esc ---------------------------------------------------------


class _FakeStream:
    def __init__(self):
        self.stopped = self.closed = False

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


def test_cancel_throws_audio_away(monkeypatch):
    """Esc обязан выбросить звук: ни распознавания, ни вставки текста."""
    module = _load(monkeypatch, _fake()[0])
    rec = _idle_recorder(module)
    rec.recording = True
    rec.stream = _FakeStream()
    rec.frames = [np.ones(1000, dtype="float32")]
    rec.levels.extend([0.5, 0.6])
    rec._transcribe_and_type = lambda audio: pytest.fail("отмена не должна распознавать")
    rec._paste = lambda text: pytest.fail("отмена не должна вставлять текст")

    rec._cancel()

    assert rec.stream.stopped and rec.stream.closed, "микрофон не закрыт"
    assert rec.recording is False and rec.busy is False
    assert rec.frames == [] and len(rec.levels) == 0, "звук не выброшен"
    assert rec.notice_text() == "запись отменена"


def test_cancel_does_nothing_when_not_recording(monkeypatch):
    module = _load(monkeypatch, _fake()[0])
    rec = _idle_recorder(module)
    rec._close_stream = lambda: pytest.fail("нечего закрывать, записи не было")
    rec._cancel()
    assert rec.notice_text() is None


# --- громкость для волны ---------------------------------------------------


def test_level_grows_with_loudness(monkeypatch):
    module = _load(monkeypatch, _fake()[0])
    rec = _idle_recorder(module)

    for amplitude in (0.0, 0.01, 0.25):
        rec._on_audio(np.full((512, 1), amplitude, dtype="float32"), 512, None, None)

    quiet, mid, loud = rec.levels
    assert quiet == 0.0
    assert 0 < mid < loud <= 1.0, f"громкость не растёт: {quiet}, {mid}, {loud}"


# --- сообщения окну --------------------------------------------------------


def test_notice_expires(monkeypatch):
    module = _load(monkeypatch, _fake()[0])
    rec = _idle_recorder(module)
    rec._notify("речь не распознана", 0.15)
    assert rec.notice_text() == "речь не распознана"
    time.sleep(0.2)
    assert rec.notice_text() is None, "сообщение должно гаснуть само"


# --- иконка в лотке --------------------------------------------------------


def test_tray_palette_follows_taskbar_theme(monkeypatch):
    """На светлой панели задач иконка обязана быть тёмной, иначе её не видно."""
    module = _load(monkeypatch, _fake()[0])
    monkeypatch.setattr(module, "taskbar_is_light", lambda: True)
    assert module.tray_colors() == module.TRAY_ON_LIGHT
    monkeypatch.setattr(module, "taskbar_is_light", lambda: False)
    assert module.tray_colors() == module.TRAY_ON_DARK


def test_tray_image_has_visible_glyph(monkeypatch):
    """Картинка не должна оказаться пустой после правок рисования."""
    module = _load(monkeypatch, _fake()[0])
    img = module.tray_image("#1c1c1e").resize((16, 16))
    assert img.getbbox() is not None, "иконка пустая"
    alpha = img.getchannel("A").histogram()
    opaque = sum(count for value, count in enumerate(alpha) if value > 128)
    assert opaque > 20, f"в иконке всего {opaque} видимых точек из 256 — при сжатии исчезнет"


def test_log_lines_get_timestamp(monkeypatch):
    module = _load(monkeypatch, _fake()[0])
    written = []
    stamped = module._Stamped(types.SimpleNamespace(write=written.append, flush=lambda: None))
    stamped.write("🎙 Запись...")
    stamped.write("\n")
    assert written[0].endswith("🎙 Запись...") and written[0] != "🎙 Запись..."
    assert written[1] == "\n"  # пустой перевод строки не штампуем


def test_engine_returns_model_name_and_clean_text(monkeypatch, tmp_path):
    """Распознаватель отдаёт имя модели (оно пишется в расшифровку) и текст без краевых пробелов."""
    module = _load(monkeypatch, _fake()[0])
    monkeypatch.setattr(module, "APP_DIR", tmp_path)  # чтобы тест не сорил папками в проекте

    fake_gigaam = types.SimpleNamespace(
        load_model=lambda name, path, quantization: types.SimpleNamespace(
            recognize=lambda audio, sample_rate: " распознано гигаамом "
        )
    )
    monkeypatch.setitem(sys.modules, "onnx_asr", fake_gigaam)

    name, recognize = module.load_engine()

    assert name == module.DEFAULT_ASR_MODEL
    assert recognize(np.zeros(16000, dtype="float32")) == "распознано гигаамом"


def test_sample_saved_as_readable_wav(monkeypatch, tmp_path):
    """Копия диктовки должна открываться как 16 кГц моно и не терять длину записи."""
    import wave

    module = _load(monkeypatch, _fake()[0])
    monkeypatch.setattr(module, "DICTATION_DIR", tmp_path)
    rec = _idle_recorder(module)
    rec.model_name = "medium"

    audio = np.sin(np.linspace(0, 400, module.SAMPLE_RATE * 2)).astype("float32")  # 2 секунды тона
    rec._save_sample(audio, "проверка записи")

    wav_files = list(tmp_path.glob("*.wav"))
    assert len(wav_files) == 1, f"ожидал один wav, нашёл {wav_files}"
    with wave.open(str(wav_files[0])) as w:
        assert w.getnchannels() == 1 and w.getframerate() == module.SAMPLE_RATE
        assert w.getnframes() == module.SAMPLE_RATE * 2

    text = wav_files[0].with_suffix(".txt").read_text(encoding="utf-8")
    assert "medium" in text and "проверка записи" in text


def test_too_short_sample_not_saved(monkeypatch, tmp_path):
    """Обрывки короче MIN_SECONDS не засоряют папку — их всё равно не распознать."""
    module = _load(monkeypatch, _fake()[0])
    monkeypatch.setattr(module, "DICTATION_DIR", tmp_path)
    rec = _idle_recorder(module)
    rec.model_name = "medium"

    rec._save_sample(np.zeros(10, dtype="float32"), "(слишком короткая запись)")
    assert list(tmp_path.iterdir()) == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))


# --- сменная модель --------------------------------------------------------


def _fake_onnx(monkeypatch, seen: list):
    fake = types.SimpleNamespace(
        load_model=lambda name, path, quantization: seen.append((name, path))
        or types.SimpleNamespace(recognize=lambda audio, sample_rate: "текст"),
    )
    monkeypatch.setitem(sys.modules, "onnx_asr", fake)


def test_model_lands_next_to_program(monkeypatch, tmp_path):
    """Веса должны качаться в models/ рядом с программой, а не в скрытый кеш."""
    module = _load(monkeypatch, _fake()[0])
    monkeypatch.setattr(module, "APP_DIR", tmp_path)
    seen: list = []
    _fake_onnx(monkeypatch, seen)

    name, _ = module.load_engine("gigaam-multilingual-ctc")

    assert name == "gigaam-multilingual-ctc"
    expected = tmp_path / "models" / "gigaam-multilingual-ctc"
    assert seen == [("gigaam-multilingual-ctc", expected)], f"путь ушёл не туда: {seen}"
    assert (expected / module.MODEL_READY_MARK).exists(), "не поставлена метка «скачано целиком»"


def test_broken_download_is_refetched(monkeypatch, tmp_path):
    """Оборванная закачка не должна превращать программу в кирпич."""
    module = _load(monkeypatch, _fake()[0])
    monkeypatch.setattr(module, "APP_DIR", tmp_path)
    broken = tmp_path / "models" / "gigaam-v3-e2e-rnnt"
    broken.mkdir(parents=True)
    (broken / "кусок.onnx.incomplete").write_text("недокачано", encoding="utf-8")

    module.ensure_model_dir("gigaam-v3-e2e-rnnt")

    assert not broken.exists(), "неполная папка осталась — библиотека уйдёт в офлайн и упадёт"


def test_weights_without_mark_are_kept(monkeypatch, tmp_path):
    """Папка из старой сборки метки не имеет — её надо пометить, а не выбросить."""
    module = _load(monkeypatch, _fake()[0])
    monkeypatch.setattr(module, "APP_DIR", tmp_path)
    folder = tmp_path / "models" / "gigaam-v3-e2e-rnnt"
    folder.mkdir(parents=True)
    (folder / "encoder.int8.onnx").write_text("веса", encoding="utf-8")

    module.ensure_model_dir("gigaam-v3-e2e-rnnt")

    assert (folder / "encoder.int8.onnx").exists(), "готовые веса удалять нельзя"
    assert (folder / module.MODEL_READY_MARK).exists()


# --- настройки из меню в лотке ---------------------------------------------


def test_failed_switch_keeps_working_model(monkeypatch, tmp_path):
    """Не скачалась новая модель — работаем на прежней, а не превращаемся в кирпич."""
    module = _load(monkeypatch, _fake()[0])
    monkeypatch.setattr(module, "APP_DIR", tmp_path)
    rec = _idle_recorder(module)
    rec.language = "ru"
    rec._recognize = lambda audio: "старая модель"

    def explode(*args, **kwargs):
        raise OSError("сеть отвалилась")

    monkeypatch.setattr(module, "load_engine", explode)
    assert rec.switch_model("gigaam-multilingual-ctc") is False
    assert rec.asr_model == module.DEFAULT_ASR_MODEL, "остались на несуществующей модели"
    assert rec._recognize(np.zeros(10)) == "старая модель", "распознавание сломано"
    assert rec.switching is False, "флаг смены завис — запись больше не начнётся"


def test_successful_switch_is_remembered(monkeypatch, tmp_path):
    """Выбор модели должен пережить перезапуск, то есть попасть в .env."""
    module = _load(monkeypatch, _fake()[0])
    monkeypatch.setattr(module, "APP_DIR", tmp_path)
    monkeypatch.syspath_prepend(str(ROOT))
    rec = _idle_recorder(module)
    rec.language = "ru"
    monkeypatch.setattr(module, "load_engine", lambda *a: ("gigaam-multilingual-ctc", lambda x: ""))

    assert rec.switch_model("gigaam-multilingual-ctc") is True

    import voice_settings

    assert voice_settings.read_all(tmp_path / ".env")["ASR_MODEL"] == "gigaam-multilingual-ctc"


def test_switch_refused_during_recording(monkeypatch, tmp_path):
    module = _load(monkeypatch, _fake()[0])
    monkeypatch.setattr(module, "APP_DIR", tmp_path)
    rec = _idle_recorder(module)
    rec.recording = True
    monkeypatch.setattr(module, "load_engine", lambda *a: pytest.fail("нельзя менять во время записи"))
    assert rec.switch_model("gigaam-multilingual-ctc") is False


def test_saving_toggle_is_remembered(monkeypatch, tmp_path):
    module = _load(monkeypatch, _fake()[0])
    monkeypatch.setattr(module, "APP_DIR", tmp_path)
    monkeypatch.syspath_prepend(str(ROOT))
    rec = _idle_recorder(module)

    rec.set_save_samples(False)

    import voice_settings

    assert rec.save_samples is False
    assert voice_settings.read_all(tmp_path / ".env")["VOICE_SAVE_SAMPLES"] == "0"


def test_recording_not_started_while_model_changes(monkeypatch):
    """Пока качается модель, нажатие клавиши не должно уходить в микрофон."""
    module = _load(monkeypatch, _fake()[0])
    rec = _idle_recorder(module)
    rec.switching = True
    rec._start = lambda: pytest.fail("запись началась во время смены модели")

    rec._toggle()

    assert rec.notice_text() == "модель ещё готовится — подожди"


def _fake_pystray(monkeypatch):
    """Подделка лотка: запоминает пункты меню и обращения к update_menu."""

    class Item:
        def __init__(self, text, action=None, **kw):
            self.text, self.action, self.kw = text, action, kw

    class Menu:
        SEPARATOR = object()

        def __init__(self, *items):
            self.items = items

    class Icon:
        def __init__(self, name, image, title, menu=None):
            self.menu = menu
            self.refreshes = 0

        def update_menu(self):
            self.refreshes += 1

        def run(self):
            pass

    monkeypatch.setitem(
        sys.modules, "pystray", types.SimpleNamespace(Icon=Icon, Menu=Menu, MenuItem=Item)
    )
    return Item


def _menu_item(icon, needle):
    for item in icon.menu.items:
        if not hasattr(item, "text"):  # разделитель
            continue
        text = item.text if isinstance(item.text, str) else item.text(item)
        if needle in text:
            return item
    raise AssertionError(f"пункта «{needle}» в меню нет")


def test_menu_is_rebuilt_after_settings_change(monkeypatch, tmp_path):
    """Windows кеширует меню: без пересборки подпись пункта врёт до перезапуска."""
    module = _load(monkeypatch, _fake()[0])
    _fake_pystray(monkeypatch)
    monkeypatch.setattr(module, "APP_DIR", tmp_path)
    monkeypatch.syspath_prepend(str(ROOT))
    rec = _idle_recorder(module)
    hotkey = types.SimpleNamespace(key="f8")

    icon = module.start_tray(rec, threading.Event(), hotkey)
    item = _menu_item(icon, "Сохранять записи")
    item.action(icon, item)
    for _ in range(100):  # работа уходит в поток, ждём его недолго
        if icon.refreshes:
            break
        time.sleep(0.01)

    assert rec.save_samples is False
    assert icon.refreshes == 1


def test_hotkey_label_follows_the_key(monkeypatch):
    """Подпись пункта считается на лету — иначе после смены клавиши в ней старая."""
    module = _load(monkeypatch, _fake()[0])
    _fake_pystray(monkeypatch)
    rec = _idle_recorder(module)
    hotkey = types.SimpleNamespace(key="f8")

    icon = module.start_tray(rec, threading.Event(), hotkey)
    hotkey.key = "f7"

    assert _menu_item(icon, "Горячая клавиша").text(None) == "Горячая клавиша: F7"


# --- загрузка модели --------------------------------------------------------


def test_model_is_not_loaded_in_constructor(monkeypatch, tmp_path):
    """Первый запуск качает сотни мегабайт. Окно должно появиться до этого, а не после."""
    module = _load(monkeypatch, _fake()[0])
    monkeypatch.setattr(module, "APP_DIR", tmp_path)
    monkeypatch.setattr(
        module, "load_engine",
        lambda *a, **kw: pytest.fail("модель грузится в конструкторе — окна ещё нет"),
    )

    rec = module.Recorder()

    assert rec.switching is True, "до загрузки запись начинаться не должна"
    rec._start = lambda: pytest.fail("запись пошла без модели")
    rec._toggle()
    assert rec.notice_text() == "модель ещё готовится — подожди"


def test_load_failure_leaves_program_usable(monkeypatch, tmp_path):
    """Не скачалась модель — программа живёт: значок, меню, внятное сообщение."""
    module = _load(monkeypatch, _fake()[0])
    monkeypatch.setattr(module, "APP_DIR", tmp_path)

    def explode(*args, **kwargs):
        raise OSError("сеть отвалилась")

    monkeypatch.setattr(module, "load_engine", explode)
    rec = module.Recorder()

    rec.load()

    assert rec.switching is False, "флаг завис — меню больше ничего не сменит"
    rec._start = lambda: pytest.fail("запись пошла без модели")
    rec._toggle()
    assert "не загрузилась" in rec.notice_text()


def test_download_notice_survives_key_press(monkeypatch, tmp_path):
    """Нажатие клавиши во время закачки не должно стирать «качаю модель»."""
    module = _load(monkeypatch, _fake()[0])
    monkeypatch.setattr(module, "APP_DIR", tmp_path)
    rec = module.Recorder()
    rec._notify("первый запуск: качаю модель, это несколько минут…", 3600)

    rec._toggle()

    assert rec.notice_text().startswith("первый запуск")
