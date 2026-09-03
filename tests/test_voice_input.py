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
    rec._model = object()  # заглушка: управлению хватает того, что она не None
    rec._vad = None
    rec.language = "ru"
    rec._punctuator = None
    rec.punctuate = True
    rec._paste_hooks = []
    rec.target_hwnd = 12345
    rec._dictionary = _NoDictionary()
    return rec


class _NoDictionary:
    """Словарь-пустышка: отдаёт текст как есть."""

    def apply(self, text):
        return text


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

    name, recognize, model = module.load_engine()

    assert name == module.DEFAULT_ASR_MODEL
    assert model is not None, "саму модель тоже надо отдавать — её просит расшифровка файлов"
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

    name, _recognize, _model = module.load_engine("gigaam-multilingual-ctc")

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
    monkeypatch.setattr(module, "load_engine",
                        lambda *a: ("gigaam-multilingual-ctc", lambda x: "", object()))

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


def test_log_falls_back_when_folder_is_closed_for_writing(monkeypatch, tmp_path):
    """Распаковали в Program Files — программа обязана жить, а не умирать молча."""
    import tempfile

    module = _load(monkeypatch, _fake()[0])
    blocker = tmp_path / "это-файл-а-не-папка"
    blocker.write_text("рядом с таким «каталогом» mkdir не сработает", encoding="utf-8")
    monkeypatch.setattr(module, "APP_DIR", blocker)

    log = module.open_log()
    try:
        assert Path(log.name).parent == Path(tempfile.gettempdir())
    finally:
        log.close()


# --- поиск причины, когда программа не запустилась -------------------------


def test_busy_port_alone_does_not_stop_the_program(monkeypatch):
    """Занятый порт — не доказательство, что копия уже работает.

    Его мог взять кто угодно, а после недавнего выхода Windows держит порт ещё
    пару минут. Раньше программа на этом молча закрывалась, и у получателя она
    просто «не запускалась» без объяснений.
    """
    module = _load(monkeypatch, _fake()[0])
    monkeypatch.setattr(module, "talk_to_running_copy", lambda files: False)

    class _Deaf:
        def setsockopt(self, *a):
            pass

        def bind(self, *a):
            raise OSError(10048, "адрес уже используется")

        def close(self):
            pass

    monkeypatch.setitem(sys.modules, "socket", types.SimpleNamespace(
        socket=lambda: _Deaf(), SOL_SOCKET=1, SO_REUSEADDR=4))

    assert module.ensure_single_instance() is None, "должны запуститься, пусть и без замка"


def test_answering_port_means_a_real_second_copy(monkeypatch):
    """А вот если на том конце ответили — там правда наша копия, второй быть не должно."""
    module = _load(monkeypatch, _fake()[0])
    monkeypatch.setattr(module, "talk_to_running_copy", lambda files: True)

    class _Deaf:
        def setsockopt(self, *a):
            pass

        def bind(self, *a):
            raise OSError(10048, "адрес уже используется")

        def close(self):
            pass

    monkeypatch.setitem(sys.modules, "socket", types.SimpleNamespace(
        socket=lambda: _Deaf(), SOL_SOCKET=1, SO_REUSEADDR=4))

    with pytest.raises(SystemExit):
        module.ensure_single_instance()


def test_listener_survives_a_missing_lock(monkeypatch):
    """Без замка слушать нечего, но падать на этом нельзя."""
    module = _load(monkeypatch, _fake()[0])
    module.listen_for_files(None, lambda paths: pytest.fail("слушать нечего"))


def test_crash_is_written_down_and_shown(monkeypatch, tmp_path, capsys):
    """Ошибка, которую никто не поймал, обязана попасть и в журнал, и на экран.

    В сборке без консоли traceback уходит в никуда: программа просто исчезает,
    и человек говорит «не запускается». Так и случилось у первого получателя.
    """
    module = _load(monkeypatch, _fake()[0])
    monkeypatch.setattr(module, "APP_DIR", tmp_path)
    shown = []
    monkeypatch.setattr(module, "show_error", shown.append)

    try:
        raise ValueError("модель не нашлась")
    except ValueError:
        module.report_crash(*sys.exc_info())

    written = capsys.readouterr().out
    assert "НЕОБРАБОТАННАЯ ОШИБКА" in written
    assert "модель не нашлась" in written and "Traceback" in written
    assert shown and "модель не нашлась" in shown[0]
    assert "dictum.log" in shown[0], "человеку надо сказать, какой файл прислать"


def test_environment_report_names_what_usually_breaks(monkeypatch, tmp_path):
    """По чужому журналу должно быть видно: версия, папка, система, есть ли модель."""
    module = _load(monkeypatch, _fake()[0])
    monkeypatch.setattr(module, "APP_DIR", tmp_path)
    (tmp_path / "models" / "gigaam-v3-e2e-rnnt").mkdir(parents=True)

    text = module.describe_environment()

    assert module.APP_VERSION in text
    assert str(tmp_path) in text
    assert "gigaam-v3-e2e-rnnt" in text, "видно ли модель — первый вопрос при разборе"
    assert "свободно на диске" in text


def test_missing_models_are_named_as_missing(monkeypatch, tmp_path):
    module = _load(monkeypatch, _fake()[0])
    monkeypatch.setattr(module, "APP_DIR", tmp_path)
    assert "нет, будут скачаны" in module.describe_environment()


def test_huge_log_is_set_aside(monkeypatch, tmp_path):
    """Журнал не должен расти вечно: у получателя цикл ошибок забил бы диск."""
    module = _load(monkeypatch, _fake()[0])
    monkeypatch.setattr(module, "APP_DIR", tmp_path)
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "dictum.log").write_text("x" * (module.LOG_LIMIT + 10), encoding="utf-8")

    handle = module.open_log()
    handle.close()

    assert (logs / "dictum.old.log").exists(), "старый журнал должен сохраниться рядом"
    assert (logs / "dictum.log").stat().st_size == 0, "новый начинается с чистого листа"


def test_import_does_not_hijack_output_and_errors():
    """Импорт ради констант не должен подменять чужой вывод и ставить окно на ошибки.

    Ловушка ошибок показывает модальное окно и ждёт клика. Установленная при
    импорте, она замораживала сборку: ошибка в build_exe.py вместо трассировки
    в консоли выводила окно, а вывод сборки уходил в журнал программы.
    """
    import subprocess
    import sys as _sys
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parent.parent
    proba = subprocess.run(
        [_sys.executable, "-c",
         "import sys; before = sys.excepthook;"
         " import voice_input;"
         " print('hijacked' if sys.excepthook is not before else 'clean')"],
        cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert "clean" in proba.stdout, proba.stdout + proba.stderr


def test_russkoy_modeli_punktuator_ne_primenyaetsya(monkeypatch):
    """У русской e2e-модели знаки свои. Второй проход поставил бы их дважды,
    и в тексте пошли бы «..» — поэтому она пропускается по имени, а не по вере."""
    module = _load(monkeypatch, _fake()[0])
    rec = _idle_recorder(module)
    rec.asr_model = "gigaam-v3-e2e-rnnt"

    def взорвись(_dir):
        raise AssertionError("пунктуатор не должен грузиться для русской модели")

    monkeypatch.setitem(sys.modules, "punctuate",
                        types.SimpleNamespace(load=взорвись))

    assert rec._polish("Привет. Как дела?") == "Привет. Как дела?"


def test_vyklyuchatel_otmenyaet_punktuatsiyu(monkeypatch):
    """Выключено в меню — пунктуатор не грузится вовсе, а не грузится вхолостую."""
    module = _load(monkeypatch, _fake()[0])
    rec = _idle_recorder(module)
    rec.asr_model = "gigaam-multilingual-ctc"
    rec.punctuate = False

    def взорвись(_dir):
        raise AssertionError("выключенный пунктуатор не должен грузиться")

    monkeypatch.setitem(sys.modules, "punctuate", types.SimpleNamespace(load=взорвись))

    assert rec._polish("рахмет сізге") == "рахмет сізге"


def test_slomannyy_punktuator_ne_ronyaet_diktovku(monkeypatch):
    """Текст уже распознан. Отдать его без запятых лучше, чем не отдать вовсе."""
    module = _load(monkeypatch, _fake()[0])
    rec = _idle_recorder(module)
    rec.asr_model = "gigaam-multilingual-ctc"

    def взорвись(_dir):
        raise RuntimeError("весов нет")

    monkeypatch.setitem(sys.modules, "punctuate", types.SimpleNamespace(load=взорвись))

    assert rec._polish("рахмет сізге") == "рахмет сізге"


class _Clipboard:
    """Буфер обмена как объект: подменяет pyperclip и помнит, что в нём лежит."""

    def __init__(self, start: str = ""):
        self.value = start

    def copy(self, text):
        self.value = text

    def paste(self):
        return self.value


def _fake_clipboard(monkeypatch, start: str = ""):
    board = _Clipboard(start)
    monkeypatch.setitem(sys.modules, "pyperclip",
                        types.SimpleNamespace(copy=board.copy, paste=board.paste))
    return board


def _fake_kb(monkeypatch):
    sent, hooks = [], []
    fake = types.SimpleNamespace(
        send=lambda combo: sent.append(combo),
        add_hotkey=lambda combo, callback, **kw: hooks.append((combo, callback)) or combo,
        remove_hotkey=lambda handle: None,
    )
    monkeypatch.setitem(sys.modules, "keyboard", fake)
    return sent, hooks


def test_promah_ne_vstavlyaet_vslepuyu_a_ostavlyaet_v_bufere(monkeypatch):
    """Окно не вышло на передний план — Ctrl+V ушёл бы в случайное окно.
    Лучше оставить текст в буфере и сказать об этом человеку."""
    module = _load(monkeypatch, _fake()[0])
    rec = _idle_recorder(module)
    board = _fake_clipboard(monkeypatch, "прежнее содержимое")
    sent, hooks = _fake_kb(monkeypatch)
    rec._focus_target = lambda: False

    rec._paste("расшифрованный текст")

    assert sent == [], "вслепую вставлять нельзя"
    assert board.value == "расшифрованный текст", "текст должен остаться в буфере"
    assert [combo for combo, _ in hooks] == ["ctrl+v", "shift+insert"]


def test_popadanie_vstavlyaet_samo(monkeypatch):
    module = _load(monkeypatch, _fake()[0])
    rec = _idle_recorder(module)
    board = _fake_clipboard(monkeypatch, "прежнее содержимое")
    sent, _hooks = _fake_kb(monkeypatch)
    rec._focus_target = lambda: True

    rec._paste("расшифрованный текст")

    assert sent == ["ctrl+v"]
    assert board.value == "расшифрованный текст"


def test_posle_ruchnoy_vstavki_vozvrashchaetsya_prezhniy_bufer(monkeypatch):
    """Ради этого всё и затевалось: вставил сам — прежнее содержимое вернулось."""
    module = _load(monkeypatch, _fake()[0])
    rec = _idle_recorder(module)
    board = _fake_clipboard(monkeypatch, "ссылка, которую я держал")
    _sent, hooks = _fake_kb(monkeypatch)
    rec._focus_target = lambda: False

    rec._paste("расшифрованный текст")
    rec._put_back("ссылка, которую я держал", "расшифрованный текст")

    assert board.value == "ссылка, которую я держал"
    assert hooks, "слежение за вставкой должно было встать"


def test_vozvrat_ne_zatiraet_to_chto_skopiroval_chelovek(monkeypatch):
    """Человек успел скопировать своё — наш возврат обязан отступить."""
    module = _load(monkeypatch, _fake()[0])
    rec = _idle_recorder(module)
    board = _fake_clipboard(monkeypatch, "прежнее")
    _fake_kb(monkeypatch)

    board.copy("человек скопировал своё")
    rec._put_back("прежнее", "расшифрованный текст")

    assert board.value == "человек скопировал своё"


def test_kartinka_v_bufere_ne_zatiraetsya_pustotoy(monkeypatch):
    """pyperclip умеет только текст: на картинке он отдаёт пустую строку.
    Восстановить пустоту — значит стереть картинку, которую человек скопировал."""
    module = _load(monkeypatch, _fake()[0])
    rec = _idle_recorder(module)
    board = _fake_clipboard(monkeypatch, "")  # как будто в буфере картинка
    _sent, hooks = _fake_kb(monkeypatch)
    rec._focus_target = lambda: False

    rec._paste("расшифрованный текст")

    assert hooks == [], "возвращать нечего — слежение вставать не должно"


def test_zanyatyy_bufer_ne_ronyaet_diktovku(monkeypatch):
    """Менеджеры буфера (Win+V, Ditto) держат буфер под замком, и запись может
    не пройти. Текст уже распознан — терять его из-за этого нельзя."""
    module = _load(monkeypatch, _fake()[0])
    rec = _idle_recorder(module)
    sent, _hooks = _fake_kb(monkeypatch)

    def занято(_text):
        raise RuntimeError("Error calling OpenClipboard")

    monkeypatch.setitem(sys.modules, "pyperclip",
                        types.SimpleNamespace(copy=занято, paste=lambda: "прежнее"))
    rec._focus_target = lambda: True

    rec._paste("расшифрованный текст")  # не должно бросить наружу

    assert sent == [], "вставлять нечего — в буфере не наш текст"
    assert "буфер занят" in rec._notice[0]


def test_module_ready_vidit_tolko_papku_s_vesami(monkeypatch, tmp_path):
    """Модуль готов, если в его папке лежит файл весов. Пустая папка остаётся
    от оборванной закачки — по ней модуль выглядел бы готовым."""
    module = _load(monkeypatch, _fake()[0])
    monkeypatch.setattr(module, "APP_DIR", tmp_path)
    folder = tmp_path / "models" / "gigaam-multilingual-ctc"
    folder.mkdir(parents=True)

    assert module.module_ready("gigaam-multilingual-ctc") is False
    (folder / "multilingual_ctc.int8.onnx").write_bytes(b"")
    assert module.module_ready("gigaam-multilingual-ctc") is True


def test_u_kazhdoy_modeli_est_razmer(monkeypatch):
    """Пометка «скачать 225 МБ» строится из MODEL_SIZES. Модель без размера
    оставит человека без предупреждения о четверти гигабайта трафика."""
    module = _load(monkeypatch, _fake()[0])

    assert set(module.ASR_MODELS) <= set(module.MODEL_SIZES)
    assert all(module.MODEL_SIZES[name] for name in module.ASR_MODELS)


def test_podpisi_modeley_korotkie(monkeypatch):
    """Подробности живут в справке. Подпись длиной в строку в меню не читают."""
    module = _load(monkeypatch, _fake()[0])

    for name, label in module.ASR_MODELS.items():
        assert len(label) <= 25, f"{name}: подпись «{label}» длиной {len(label)}"


def test_skachat_pishetsya_tolko_u_otsutstvuyushchih(monkeypatch, tmp_path):
    module = _load(monkeypatch, _fake()[0])
    monkeypatch.setattr(module, "APP_DIR", tmp_path)
    folder = tmp_path / "models" / "gigaam-v3-e2e-rnnt"
    folder.mkdir(parents=True)
    (folder / "веса.onnx").write_bytes(b"")

    assert module.model_label("gigaam-v3-e2e-rnnt", "Русский") == "Русский"
    assert module.model_label("gigaam-multilingual-ctc", "Многоязычная") == \
        "Многоязычная — скачать 225 МБ"


def test_kopiruet_poslednyuyu_diktovku(monkeypatch):
    """Страховка на случай, когда автоматика промахнулась, а вставку правой
    кнопкой мыши мы не увидели: текст всегда можно забрать из меню."""
    module = _load(monkeypatch, _fake()[0])
    rec = _idle_recorder(module)
    board = _fake_clipboard(monkeypatch, "что-то другое")
    rec.last_text = "текст последней диктовки"

    rec.copy_last_text()

    assert board.value == "текст последней диктовки"


def test_kopirovat_nechego_kogda_diktovok_ne_bylo(monkeypatch):
    module = _load(monkeypatch, _fake()[0])
    rec = _idle_recorder(module)
    board = _fake_clipboard(monkeypatch, "что-то другое")
    rec.last_text = ""

    rec.copy_last_text()

    assert board.value == "что-то другое", "пустотой буфер затирать нельзя"


def test_soobshchenie_o_podgotovke_ne_perezhivaet_rabotu(monkeypatch):
    """Сообщение с большим сроком показа держало капсулу на экране минуту после
    того, как работа кончилась. Со стороны это выглядит зависанием — так и вышло
    у первого же проверяющего: текст вставился, а окно осталось висеть."""
    module = _load(monkeypatch, _fake()[0])
    rec = _idle_recorder(module)
    rec.asr_model = "gigaam-multilingual-ctc"
    monkeypatch.setitem(sys.modules, "punctuate", types.SimpleNamespace(
        load=lambda _dir: types.SimpleNamespace(apply=lambda t: t + ".")))

    assert rec._polish("рахмет") == "рахмет."
    assert rec.notice_text() is None, "капсула останется висеть на экране"


def test_soobshchenie_snimaetsya_i_kogda_podgotovka_upala(monkeypatch):
    """Упавшая загрузка — тем более не повод оставлять окно на экране."""
    module = _load(monkeypatch, _fake()[0])
    rec = _idle_recorder(module)
    rec.asr_model = "gigaam-multilingual-ctc"

    def упало(_dir):
        raise RuntimeError("весов нет")

    monkeypatch.setitem(sys.modules, "punctuate", types.SimpleNamespace(load=упало))

    assert rec._polish("рахмет") == "рахмет"
    assert rec.notice_text() is None


# --- словарь замен в связке ------------------------------------------------


class _Slovar:
    """Словарь с одним правилом — проверяем, что его вообще спрашивают."""

    def __init__(self):
        self.calls = 0

    def apply(self, text):
        self.calls += 1
        return text.replace("гугл", "Google")


def test_slovar_rabotaet_i_dlya_russkoy_modeli(monkeypatch):
    module = _load(monkeypatch, _fake())
    """Русская модель знаки ставит сама, но имён человека не знает — словарь ей нужен."""
    rec = _idle_recorder(module)
    rec.asr_model = "gigaam-v3-e2e-rnnt"
    assert rec.asr_model in module.PUNCTUATED_BY_MODEL
    rec._dictionary = _Slovar()
    assert rec._polish("открой гугл") == "открой Google"


def test_slovar_rabotaet_pri_vyklyuchennyh_znakah(monkeypatch):
    module = _load(monkeypatch, _fake())
    """Выключенные знаки препинания не должны отключать замены заодно."""
    rec = _idle_recorder(module)
    rec.asr_model = "gigaam-multilingual-ctc"
    rec.punctuate = False
    rec._dictionary = _Slovar()
    assert rec._polish("открой гугл") == "открой Google"


def test_slomannyy_slovar_ne_ronyaet_diktovku(monkeypatch):
    module = _load(monkeypatch, _fake())
    """Сбой словаря обязан отдать текст как был: диктовка уже распознана."""
    rec = _idle_recorder(module)
    rec.asr_model = "gigaam-v3-e2e-rnnt"

    class _Slomannyy:
        def apply(self, text):
            raise RuntimeError("файл словаря испорчен")

    rec._dictionary = _Slomannyy()
    assert rec._polish("текст на месте") == "текст на месте"


# --- переключатель языка ---------------------------------------------------


def test_yazyk_menyaetsya_i_zapominaetsya(monkeypatch, tmp_path):
    """Выбор из меню обязан лечь в .env — иначе после перезапуска он потеряется."""
    module = _load(monkeypatch, _fake())
    monkeypatch.setattr(module, "APP_DIR", tmp_path)
    rec = _idle_recorder(module)

    rec.set_interface_language("kk")
    assert module.messages.language() == "kk"
    assert "VOICE_LANG=kk" in (tmp_path / ".env").read_text(encoding="utf-8")
    module.messages.set_language(module.messages.DEFAULT)


def test_neizvestnyy_yazyk_ostavlyaet_prezhniy(monkeypatch, tmp_path):
    """Испорченная настройка не должна превращать меню в пустые строки."""
    module = _load(monkeypatch, _fake())
    monkeypatch.setattr(module, "APP_DIR", tmp_path)
    rec = _idle_recorder(module)

    rec.set_interface_language("эльфийский")
    assert module.messages.language() == module.messages.DEFAULT
    # В файл ложится то, что реально встало, а не то, что попросили.
    assert "VOICE_LANG=ru" in (tmp_path / ".env").read_text(encoding="utf-8")


def test_soobshchenie_o_smene_na_novom_yazyke(monkeypatch, tmp_path):
    """Единственная проверка, доступная человеку сразу: увидел слово — язык встал."""
    module = _load(monkeypatch, _fake())
    monkeypatch.setattr(module, "APP_DIR", tmp_path)
    module.messages.TEXTS["notice.language_set"]["kk"] = "бағдарлама тілі: {language}"
    rec = _idle_recorder(module)
    try:
        rec.set_interface_language("kk")
        assert rec.notice_text() == "бағдарлама тілі: Қазақша"
    finally:
        module.messages.TEXTS["notice.language_set"].pop("kk", None)
        module.messages.set_language(module.messages.DEFAULT)
