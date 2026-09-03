"""Расшифровка файлов: чтение звука, нарезка, куда ложится текст."""

import importlib.util
import sys
import types
import wave
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("transcribe", ROOT / "transcribe.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _wav(path: Path, seconds: float = 1.0, rate: int = 16000, channels: int = 1) -> Path:
    """Настоящий wav с тоном: тишина не годится, на ней не видно сведения каналов."""
    tone = np.sin(np.linspace(0, 400 * seconds, int(rate * seconds))).astype("float32")
    data = np.stack([tone] * channels, axis=1) if channels > 1 else tone[:, None]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(data, -1, 1) * 32767).astype("<i2").tobytes())
    return path


# --- чтение файла ----------------------------------------------------------


def test_reads_wav_as_is(tmp_path):
    module = _load()
    audio = module.read_audio(_wav(tmp_path / "проба.wav", seconds=2.0))
    assert audio.dtype == np.float32
    assert abs(len(audio) - 2 * module.SAMPLE_RATE) < 10


def test_resamples_to_what_the_model_wants(tmp_path):
    """Телефоны пишут 44,1 кГц, модель принимает только 16 — приводить обязаны мы."""
    module = _load()
    audio = module.read_audio(_wav(tmp_path / "телефон.wav", seconds=3.0, rate=44100))
    assert abs(len(audio) - 3 * module.SAMPLE_RATE) < module.SAMPLE_RATE * 0.02, \
        "длина в секундах должна сохраниться, а частота стать 16 кГц"


def test_stereo_becomes_mono(tmp_path):
    module = _load()
    audio = module.read_audio(_wav(tmp_path / "стерео.wav", seconds=1.0, channels=2))
    assert audio.ndim == 1, "модель принимает один канал"


def test_unreadable_format_explains_itself(tmp_path):
    """Человеку нужно понять, что делать, а не увидеть чужую ошибку библиотеки."""
    module = _load()
    fake = tmp_path / "запись.m4a"
    fake.write_bytes(b"\x00" * 100)
    with pytest.raises(module.AudioError) as stop:
        module.read_audio(fake)
    assert "m4a" in str(stop.value) and "MP3" in str(stop.value)


def test_empty_file_is_not_a_crash(tmp_path):
    module = _load()
    with pytest.raises(module.AudioError):
        module.read_audio(_wav(tmp_path / "пусто.wav", seconds=0))


# --- расшифровка -----------------------------------------------------------


class _FakeModel:
    """Модель, которая режет по тишине: отдаёт куски, как настоящая с VAD."""

    def __init__(self, segments):
        self.segments = segments
        self.vad_used = None

    def with_vad(self, vad, **options):
        self.vad_used = vad
        self.options = options
        return self

    def recognize(self, audio, sample_rate):
        return iter(self.segments)


def _segment(start, end, text):
    return types.SimpleNamespace(start=start, end=end, text=text)


def test_segments_become_paragraphs():
    module = _load()
    model = _FakeModel([_segment(0, 5, " первый кусок "), _segment(8, 10, "второй кусок")])
    text = module.transcribe(np.zeros(module.SAMPLE_RATE * 10, dtype="float32"), model, "нарезчик")
    assert text == "первый кусок\n\nвторой кусок"
    assert model.vad_used == "нарезчик", "нарезка обязана применяться, иначе текст теряется"


def test_empty_segments_are_dropped():
    module = _load()
    model = _FakeModel([_segment(0, 5, "слова"), _segment(6, 10, "   ")])
    text = module.transcribe(np.zeros(module.SAMPLE_RATE * 10, dtype="float32"), model, None)
    assert text == "слова"


def test_progress_reaches_the_end():
    """Расшифровка часа идёт минутами: без признаков жизни это выглядит зависанием."""
    module = _load()
    model = _FakeModel([_segment(0, 30, "а"), _segment(30, 60, "б")])
    seen = []
    module.transcribe(np.zeros(module.SAMPLE_RATE * 60, dtype="float32"), model, None, seen.append)
    assert seen and seen[-1] == 1.0
    assert all(0 <= value <= 1 for value in seen), f"доля вне 0..1: {seen}"


# --- куда ложится текст ----------------------------------------------------


def test_text_lands_next_to_the_recording(tmp_path):
    module = _load()
    audio = _wav(tmp_path / "совещание.wav")
    target = module.save(audio, "расшифровка", "gigaam-v3-e2e-rnnt", 125.0)
    assert target == tmp_path / "совещание.txt"
    written = target.read_text(encoding="utf-8")
    assert "расшифровка" in written
    assert "совещание.wav" in written and "gigaam-v3-e2e-rnnt" in written, \
        "через месяц должно быть понятно, откуда текст и чем сделан"


def test_existing_text_is_never_overwritten(tmp_path):
    """Расшифровку сделать заново дёшево, а затёртый чужой текст не вернуть."""
    module = _load()
    audio = _wav(tmp_path / "заметка.wav")
    (tmp_path / "заметка.txt").write_text("чужое", encoding="utf-8")

    target = module.save(audio, "новое", "модель", 10.0)

    assert target.name == "заметка (2).txt"
    assert (tmp_path / "заметка.txt").read_text(encoding="utf-8") == "чужое"


def test_short_pauses_do_not_break_the_paragraph():
    """Резать по вдохам нельзя: текст рассыпается на огрызки «Нас», «А мы», «Не»."""
    module = _load()
    model = _FakeModel([
        _segment(0.0, 3.0, "мы это не"),
        _segment(3.2, 5.0, "рассматривали"),       # вдох, тот же абзац
        _segment(9.0, 12.0, "теперь о другом"),    # долгая пауза, новый абзац
    ])
    text = module.transcribe(np.zeros(module.SAMPLE_RATE * 12, dtype="float32"), model, None)
    assert text == "мы это не рассматривали\n\nтеперь о другом"


def test_chunks_stay_inside_what_the_model_handles():
    """Модель обучена на кусках до полминуты — на длинных она молча теряет текст."""
    module = _load()

    class _Checking(_FakeModel):
        def with_vad(self, vad, **options):
            self.options = options
            return self

    model = _Checking([_segment(0, 5, "слова")])
    module.transcribe(np.zeros(module.SAMPLE_RATE * 5, dtype="float32"), model, None)
    assert model.options["max_speech_duration_s"] <= 30
    assert model.options["min_silence_duration_ms"] >= 300, "меньший порог режет на вдохах"


def test_polish_primenyaetsya_k_kazhdomu_kusku():
    """Знаки ставятся по кускам, разделённым паузами: границы естественные,
    по дыханию говорящего, и каждый кусок заведомо влезает в окно пунктуатора."""
    module = _load()
    model = _FakeModel([_segment(0.0, 1.0, "бір екі"), _segment(1.2, 2.0, "үш төрт")])
    seen = []

    def polish(text):
        seen.append(text)
        return text.capitalize() + "."

    text = module.transcribe(np.zeros(module.SAMPLE_RATE * 2, dtype="float32"), model, None,
                             polish=polish)

    assert seen == ["бір екі", "үш төрт"], "доводка должна получать куски, а не весь текст"
    assert text == "Бір екі. Үш төрт."


def test_bez_polish_tekst_ne_menyaetsya():
    """Русской модели доводка не нужна — она не должна вызываться вовсе."""
    module = _load()
    model = _FakeModel([_segment(0, 1, "как было")])
    text = module.transcribe(np.zeros(module.SAMPLE_RATE, dtype="float32"), model, None)
    assert text == "как было"
