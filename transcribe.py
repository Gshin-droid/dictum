"""Расшифровка готовых аудиофайлов в текст.

Про микрофон, окно и лоток не знает ничего: получает путь к файлу и уже
загруженные модели, отдаёт текст. Поэтому проверяется без звуковой карты.

Две вещи, которые здесь неочевидны и без которых модуль был бы бесполезен:

1. **Нарезка по тишине обязательна.** Модель обучена на кусках до полминуты, и
   на длинном входе молча теряет текст: замер на трёхминутной записи дал 164
   слова вместо 275 — сорок процентов пропало без единой ошибки в логе. Режет
   не наш код, а onnx-asr через модель Silero; нам остаётся склеить куски.

2. **Частоту дискретизации приводим сами.** Модель принимает только 8 или 16 кГц,
   а телефоны и диктофоны пишут 44,1 и 48. Пересэмплер onnx-asr умеет лишь
   8→16, поэтому берём soxr — 170 КБ и правильная фильтрация. Писать своё тут
   нельзя: наивное прореживание даёт наложение частот, речь глохнет, а по логам
   это невидимо.
"""

from pathlib import Path

import numpy as np
import soundfile as sf
import soxr

SAMPLE_RATE = 16000

# Что умеет читать libsndfile. Расширения, а не имена форматов: человеку нужны
# они. .opus и .oga живут внутри контейнера OGG, поэтому в списке форматов их нет.
SUPPORTED = (".wav", ".mp3", ".ogg", ".opus", ".oga", ".flac", ".aiff", ".aif",
             ".au", ".caf", ".w64", ".wave", ".aifc")
# m4a/aac не читается: формат закрыт патентами, libsndfile его не поддерживает
NOT_SUPPORTED_HINT = (
    "Формат не поддерживается. Читаются: WAV, MP3, OGG, OPUS, FLAC, AIFF, CAF.\n"
    "Записи с iPhone (.m4a) и звук из видео — сначала перегнать в MP3 или WAV."
)


class AudioError(Exception):
    """Файл не прочитался. Сообщение уже написано по-русски для человека."""


def read_audio(path: Path, rate: int = SAMPLE_RATE) -> np.ndarray:
    """Читает файл в моно нужной частоты. Стерео сводится усреднением каналов."""
    try:
        data, source_rate = sf.read(str(path), dtype="float32", always_2d=True)
    except Exception as exc:
        if path.suffix.lower() not in SUPPORTED:
            raise AudioError(NOT_SUPPORTED_HINT) from exc
        raise AudioError(f"Не смог прочитать файл: {exc}") from exc

    if data.shape[0] == 0:
        raise AudioError("В файле нет звука")

    audio = data.mean(axis=1) if data.shape[1] > 1 else data[:, 0]
    if source_rate != rate:
        audio = soxr.resample(audio, source_rate, rate)
    return np.ascontiguousarray(audio, dtype="float32")


# Нарезка служит двум разным целям, и настройки у них разные.
# Для модели: куски не длиннее её привычных тридцати секунд.
MAX_CHUNK_SECONDS = 25
# Чтобы не резать на вдохах: паузой считается только полсекунды тишины. С меньшим
# порогом текст рассыпается на огрызки вроде «Нас», «А мы», «Не» — проверено.
PAUSE_MS = 500
# Абзац начинается только с долгой паузы. Секунды мало: столько человек молчит,
# подбирая слово посреди фразы, и текст рассыпался на «А мы», «Не», «Рассматривали».
PARAGRAPH_GAP = 2.0


def transcribe(audio: np.ndarray, model, vad, on_progress=None, polish=None) -> str:
    """Звук → текст. Длинные паузы становятся границами абзацев.

    on_progress зовётся с долей сделанного от 0 до 1: расшифровка часа идёт
    минутами, и без признаков жизни это выглядит как зависание.

    polish — необязательная доводка каждого куска, «текст → текст». Через неё
    приходят знаки препинания для многоязычной модели. Модуль о ней ничего не
    знает, кроме того, что это функция: так он остаётся проверяемым без весов,
    и так же он однажды переедет на сервер, не потянув за собой пунктуатор.
    """
    total = len(audio) / SAMPLE_RATE
    paragraphs: list[list[str]] = []
    previous_end = None

    chunks = model.with_vad(
        vad, min_silence_duration_ms=PAUSE_MS, max_speech_duration_s=MAX_CHUNK_SECONDS
    ).recognize(audio, sample_rate=SAMPLE_RATE)

    for segment in chunks:
        text = segment.text.strip()
        if text:
            if polish:
                text = polish(text)
            if previous_end is None or segment.start - previous_end >= PARAGRAPH_GAP:
                paragraphs.append([text])
            else:
                paragraphs[-1].append(text)
            previous_end = segment.end
        if on_progress and total:
            on_progress(min(1.0, segment.end / total))
    if on_progress:
        on_progress(1.0)
    return "\n\n".join(" ".join(parts) for parts in paragraphs)


def text_path(audio_path: Path) -> Path:
    """Куда положить расшифровку: рядом с записью, тем же именем.

    Занято — добавляем номер. Затирать чужой файл, о котором нас не просили,
    нельзя: расшифровка дешёвая, а перезаписанный текст не вернуть.
    """
    target = audio_path.with_suffix(".txt")
    number = 2
    while target.exists():
        target = audio_path.with_name(f"{audio_path.stem} ({number}).txt")
        number += 1
    return target


def save(audio_path: Path, text: str, model_name: str, seconds: float) -> Path:
    """Кладёт расшифровку рядом с записью. Шапка нужна, чтобы через месяц
    было понятно, откуда взялся текст и какой моделью сделан."""
    target = text_path(audio_path)
    header = (f"{audio_path.name} · {seconds / 60:.0f} мин · модель {model_name}\n"
              f"{'-' * 60}\n\n")
    target.write_text(header + text + "\n", encoding="utf-8")
    return target
