"""Dictum — голосовая диктовка для Windows: клавиша → запись → распознавание → вставка.

Открытый код, лицензия MIT: github.com/Gshin-droid/dictum

Запуск:
    dictum.exe                        собранная версия, ставить ничего не надо
    python voice_input.py             из исходников: окно + значок в лотке
    python voice_input.py --headless  без окна, только клавиша
    python voice_input.py --check     проверить микрофон и модель, выйти

В покое окна не видно, живёт иконка в системном лотке. По F8 снизу по центру
экрана всплывает капсула с волной громкости, повторное нажатие останавливает
запись и распознаёт, Esc выбрасывает запись без распознавания. После вставки
текста окно скрывается само. Выход — через меню значка в лотке.

Настройки меняются правой кнопкой по значку и ложатся в .env рядом с программой:
ASR_MODEL, VOICE_HOTKEY, VOICE_SAVE_SAMPLES, VOICE_GAIN. Распознавание идёт на
этом компьютере, интернет нужен один раз — скачать модель.
"""

import argparse
import os
import sys
import threading
import shutil
import time
import wave
import winsound
from collections import deque
from pathlib import Path

import numpy as np
import sounddevice as sd
from dotenv import load_dotenv


def _app_dir() -> Path:
    """Папка, от которой считаются свои файлы: модель, .env, логи, записи диктовок.

    В собранном exe файла-исходника не существует, поэтому корнем становится папка
    самого exe — так собранную копию можно носить на флешке целиком, вместе с
    настройками и моделью.
    """
    if getattr(sys, "frozen", False):  # признак, который ставит PyInstaller
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = _app_dir()
# ponytail: копии диктовок копятся без ограничения (~2 МБ на минуту речи).
# Чистить руками; автоудаление по возрасту добавить, если папка начнёт мешать.
DICTATION_DIR = APP_DIR / "data" / "dictation"
# Между этими моделями можно переключаться. Ключ — имя для onnx-asr, оно же имя
# папки в models/. Русская e2e сама расставляет знаки препинания, многоязычные —
# нет: это разные семейства, и пунктуацию умеет только e2e-версия.
ASR_MODELS = {
    "gigaam-v3-e2e-rnnt": "Русский, со знаками препинания",
    "gigaam-multilingual-ctc": "Русский, казахский, киргизский, узбекский — без знаков препинания",
    "gigaam-multilingual-large-ctc": "Те же языки, точнее и вдвое тяжелее",
}
DEFAULT_ASR_MODEL = "gigaam-v3-e2e-rnnt"
VAD_MODEL = "silero-vad"  # нарезчик длинных записей по тишине, ~2 МБ
# Веса рядом с программой (models/) важнее кеша: так переносная копия работает
# без интернета. Нет папки — модель скачается с HuggingFace в неё же.
MODEL_READY_MARK = ".complete"  # метка «веса скачаны целиком», см. ensure_model_dir


def ensure_model_dir(name: str) -> Path:
    """Папка весов модели. Неполную закачку удаляет, чтобы она не заблокировала запуск.

    Оборванная закачка оставляет папку с частью файлов. Библиотека, увидев любую
    существующую папку, переходит в режим «только локальные файлы» и падает — то
    есть один разрыв сети превратил бы программу в кирпич. Полноту отмечаем
    файлом-меткой: нет метки, но веса на месте — значит папка из старой сборки,
    просто ставим метку; нет ни того, ни другого — качаем заново.
    """
    folder = APP_DIR / "models" / name
    if folder.exists() and not (folder / MODEL_READY_MARK).exists():
        if any(folder.glob("*.onnx")):
            (folder / MODEL_READY_MARK).touch()
        else:
            print(f"Папка модели {name} неполная — качаю заново")
            shutil.rmtree(folder, ignore_errors=True)
    return folder
APP_NAME = "Dictum"
# Три числа: ломающее изменение . новые возможности . исправления.
# Единственное место, где версия записана: отсюда её берут «О программе», журнал
# и свойства exe, которые показывает проводник Windows.
APP_VERSION = "1.1.0"
APP_TAGLINE = f"{APP_NAME} — голосовая диктовка"
APP_AUTHOR = "Gshin-droid"
APP_URL = "github.com/Gshin-droid/dictum"

SAMPLE_RATE = 16000
MAX_SECONDS = 120  # предохранитель: авто-стоп, если забыл выключить запись
MIN_SECONDS = 0.3
SINGLE_INSTANCE_PORT = 47811  # локальный порт как замок от второго экземпляра
LEVELS_KEPT = 200  # история громкости для волны, с запасом на ширину окна
NOTICE_SECONDS = 3.0  # сколько показывать сообщение вроде «речь не распознана»


# под pythonw консоли нет — print() уходит в лог-файл
class _Stamped:
    """Дописывает время к каждой строке лога — иначе не понять, когда что было."""

    def __init__(self, stream):
        self.stream = stream

    def write(self, text: str) -> int:
        if text.strip():
            text = time.strftime("%d.%m %H:%M:%S ") + text
        return self.stream.write(text)

    def flush(self) -> None:
        self.stream.flush()


def open_log():
    """Журнал рядом с программой, а если туда писать нельзя — во временную папку.

    Переносную копию распакуют куда угодно, в том числе в Program Files, где
    обычной программе писать запрещено. Молча умереть на этой строке нельзя:
    консоли нет, и человек не увидит ни ошибки, ни программы — просто ничего.
    """
    try:
        (APP_DIR / "logs").mkdir(exist_ok=True)
        return open(APP_DIR / "logs" / "dictum.log", "a", encoding="utf-8", buffering=1)
    except OSError:
        import tempfile

        return open(Path(tempfile.gettempdir()) / "dictum.log", "a", encoding="utf-8", buffering=1)


# В сборке PyInstaller без консоли sys.stdout не пустой, а заглушка, молча
# глотающая вывод, — поэтому проверяем признак сборки, а не только пустоту.
if getattr(sys, "frozen", False) or sys.stdout is None or sys.stderr is None:
    sys.stdout = sys.stderr = _Stamped(open_log())
else:
    # консоль Windows живёт в cp1251: без этого любой ✅ в выводе роняет процесс
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def bind_hotkey(hotkey: str, callback) -> None:
    """Вешает хоткей так, чтобы он не умирал насовсем.

    keyboard.add_hotkey сверяет весь набор зажатых клавиш, а при вставке текста
    (keyboard.send) библиотека временно не видит настоящих нажатий — потерянное
    «клавишу отпустили» оставляет её зажатой навечно, и хоткей больше не совпадает.
    Хук на одиночную клавишу это состояние не смотрит. Сочетаниям выбора нет.
    """
    import keyboard

    try:
        return keyboard.on_press_key(hotkey, lambda _event: callback())
    except (ValueError, KeyError):  # это сочетание, а не одна клавиша
        return keyboard.add_hotkey(hotkey, callback)


class Hotkey:
    """Горячая клавиша диктовки: помнит текущую и умеет перевешиваться на другую."""

    def __init__(self, key: str, callback):
        self.key = key
        self.callback = callback
        self.on_change = None  # окно подписывается сюда, чтобы обновить подпись на капсуле
        self.handle = bind_hotkey(key, callback)

    def rebind(self, key: str) -> None:
        import keyboard

        import voice_settings as settings

        if self.handle is not None:
            for remove in (keyboard.unhook, keyboard.remove_hotkey):
                try:
                    remove(self.handle)
                    break
                except Exception:  # ручка бывает от любого из двух способов навески
                    continue
        self.key = key
        self.handle = bind_hotkey(key, self.callback)
        settings.write(APP_DIR / ".env", "VOICE_HOTKEY", key)
        print(f"Горячая клавиша теперь {key}")
        if self.on_change:
            self.on_change(key)


def ensure_single_instance(files: list[str] | None = None):
    """Замок от второго экземпляра — он же почтовый ящик для файлов на расшифровку.

    Порт на петле уже держался ради «запущен ли я дважды». Раз так, второй
    экземпляр не просто уходит, а сначала передаёт первому пути к файлам, которые
    на него бросили. Иначе перетаскивание файла на значок работало бы только при
    выключенной программе, а модель грузилась бы в память вторым экземпляром.
    """
    import socket

    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    try:
        sock.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
        sock.listen(4)
    except OSError:
        if files:
            try:
                with socket.create_connection(("127.0.0.1", SINGLE_INSTANCE_PORT), timeout=5) as out:
                    out.sendall("\n".join(files).encode("utf-8"))
                print(f"Передал уже запущенной программе файлов: {len(files)}")
            except OSError as exc:
                print(f"⚠️ Не смог передать файлы работающей программе: {exc}")
        else:
            print("Уже запущен другой экземпляр — выходим")
        sys.exit(0)
    return sock  # держим сокет открытым до конца работы процесса


def listen_for_files(sock, handle) -> None:
    """Принимает пути от второго экземпляра. Свой поток, чтобы не держать окно."""
    def serve():
        while True:
            try:
                client, _ = sock.accept()
            except OSError:  # сокет закрыт при выходе — это не поломка
                return
            with client:
                data = b""
                while chunk := client.recv(4096):
                    data += chunk
            paths = [line for line in data.decode("utf-8", "replace").splitlines() if line.strip()]
            if paths:
                print(f"Пришло файлов на расшифровку: {len(paths)}")
                handle(paths)

    threading.Thread(target=serve, daemon=True).start()


def load_engine(asr_model: str = DEFAULT_ASR_MODEL):
    """Готовит распознаватель: имя, функция «звук → текст» и сама модель.

    Модель отдаём третьей — она нужна расшифровке файлов: та зовёт у неё
    with_vad(), чего через готовую функцию не сделать.

    Веса ищутся в models/ рядом с программой. Папки нет — они туда и скачаются,
    после чего интернет программе больше не нужен.
    """
    import onnx_asr

    folder = ensure_model_dir(asr_model)
    if folder.exists():
        print(f"Загружаю {asr_model} (int8) из models/...")
    else:
        print(f"Скачиваю модель {asr_model} — несколько сотен мегабайт, один раз")
    # Путь передаём всегда: есть папка — работаем без сети, нет — веса лягут
    # в неё же, рядом с программой, а не в скрытый кеш пользователя.
    model = onnx_asr.load_model(asr_model, folder, quantization="int8")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / MODEL_READY_MARK).touch()
    recognize = lambda audio: model.recognize(audio, sample_rate=SAMPLE_RATE).strip()  # noqa: E731
    return asr_model, recognize, model


def load_vad():
    """Нарезчик по тишине для расшифровки файлов. Маленький, качается один раз.

    Грузится отдельно и только по надобности: диктовке он не нужен, а тянуть его
    при каждом старте — лишние секунды на пустом месте.
    """
    import onnx_asr

    folder = ensure_model_dir(VAD_MODEL)
    if not folder.exists():
        print(f"Скачиваю нарезчик по тишине {VAD_MODEL} — пара мегабайт, один раз")
    vad = onnx_asr.load_vad("silero", folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / MODEL_READY_MARK).touch()
    return vad


class Recorder:
    """Запись, распознавание и вставка текста. Окно опрашивает состояние снаружи."""

    def __init__(self, gain: float = 2.2, asr_model: str = DEFAULT_ASR_MODEL,
                 save_samples: bool = False):
        self.asr_model = asr_model
        self.save_samples = save_samples
        self.model_name = asr_model
        self._recognize = None  # модель ещё не загружена
        self._model = None  # сама модель: нужна расшифровке файлов
        self._vad = None  # нарезчик по тишине, грузится по первой надобности
        self.switching = True  # грузится или меняется модель: запись пока не начинаем
        self.gain = gain  # чувствительность волны: подкрутить, если полоски вялые или зашкаливают
        self.recording = False
        self.busy = False
        self.last_text = ""
        self.target_hwnd = 0  # окно, куда вставлять текст (фокус на момент старта записи)
        self.levels: deque[float] = deque(maxlen=LEVELS_KEPT)
        self.frames: list[np.ndarray] = []
        self.stream: sd.InputStream | None = None
        self.started_at = 0.0
        self.lock = threading.Lock()
        self._notice = ("", 0.0)
        self._esc_hook = None
        self._timer: threading.Timer | None = None

    @property
    def state(self) -> str:
        if self.recording:
            return "recording"
        if self.busy:
            return "busy"
        return "idle"

    def notice_text(self) -> str | None:
        """Сообщение, которое окно должно показать прямо сейчас (или None)."""
        text, until = self._notice
        return text if text and time.time() < until else None

    def _notify(self, text: str, seconds: float = NOTICE_SECONDS) -> None:
        self._notice = (text, time.time() + seconds)

    def announce(self, text: str, seconds: float = NOTICE_SECONDS) -> None:
        """Показать сообщение в капсуле. Нужно меню в лотке: своего окна у него нет."""
        self._notify(text, seconds)

    def load(self) -> None:
        """Готовит модель. Зовётся, когда окно и значок в лотке уже на экране.

        Первый запуск скачивает несколько сотен мегабайт. Пока это делалось до
        создания окна, человек, запустивший программу, минутами не видел ничего —
        она выглядела не запустившейся. Теперь ход загрузки виден в капсуле.
        """
        if not (APP_DIR / "models" / self.asr_model).exists():
            self._notify("первый запуск: качаю модель, это несколько минут…", 3600)
        try:
            self.model_name, self._recognize, self._model = load_engine(self.asr_model)
            self._notify("готово, можно диктовать", 4)
            print(f"Готов. Модель: {self.model_name}")
        except Exception as exc:
            self._notify("модель не загрузилась — выбери другую в меню", 600)
            print(f"⚠️ Не смог загрузить модель {self.asr_model}: {exc}")
        finally:
            self.switching = False

    def switch_model(self, name: str) -> bool:
        """Меняет модель на ходу. Не вышло — остаёмся на прежней и работаем дальше.

        Скачивание идёт минутами. Замок микрофона на это время не берём: иначе
        нажатие клавиши выглядело бы как залипший драйвер. Вместо этого поднимаем
        флаг, по которому запись вежливо отказывается начинаться.
        """
        import voice_settings as settings

        if name == self.asr_model and self._recognize is not None:
            return True
        if self.switching:  # уже идёт загрузка — второй качалки нам не надо
            self._notify("модель ещё готовится — подожди")
            return False
        if self.recording or self.busy:
            self._notify("сначала закончи диктовку")
            return False

        previous = (self.model_name, self._recognize, self._model, self.asr_model)
        self.switching = True
        self._notify(f"готовлю модель {name}, это может занять минуты…", 900)
        try:
            self.model_name, self._recognize, self._model = load_engine(name)
            self.asr_model = name
            settings.write(APP_DIR / ".env", "ASR_MODEL", name)
            self._notify("модель готова", 4)
            print(f"Модель переключена на {name}")
            return True
        except Exception as exc:
            self.model_name, self._recognize, self._model, self.asr_model = previous
            self._notify("не смог сменить модель", 6)
            print(f"⚠️ Не смог переключить модель на {name}: {exc}")
            return False
        finally:
            self.switching = False

    def transcribe_files(self, paths: list[str]) -> None:
        """Расшифровывает готовые записи. Зовётся из чужого потока, работает в своём."""
        threading.Thread(target=self._transcribe_files, args=(list(paths),), daemon=True).start()

    def _transcribe_files(self, paths: list[str]) -> None:
        import transcribe as tr

        if self.switching or self._model is None:
            self._notify("модель ещё готовится — подожди", 6)
            return
        if not self.lock.acquire(blocking=False):  # идёт диктовка — не мешаем ей
            self._notify("сначала закончи диктовку", 6)
            return
        try:
            self.busy = True  # окно покажет «занято», а клавиша не начнёт запись
            if self._vad is None:
                self._notify("готовлю нарезчик по тишине…", 300)
                self._vad = load_vad()
            for number, name in enumerate(paths, 1):
                path = Path(name)
                counter = f"{number} из {len(paths)}: " if len(paths) > 1 else ""
                try:
                    self._notify(f"{counter}читаю {path.name}", 300)
                    audio = tr.read_audio(path)
                    seconds = len(audio) / SAMPLE_RATE
                    print(f"Расшифровываю {path.name}: {seconds / 60:.1f} мин")

                    def show(done, counter=counter, seconds=seconds):
                        self._notify(f"{counter}расшифровываю: {done * 100:.0f}% "
                                     f"из {seconds / 60:.0f} мин", 300)

                    started = time.time()
                    text = tr.transcribe(audio, self._model, self._vad, show)
                    if not text:
                        self._notify(f"{path.name}: речь не распознана", 8)
                        print(f"В {path.name} речи не нашлось")
                        continue
                    target = tr.save(path, text, self.model_name, seconds)
                    print(f"→ {target.name}: слов {len(text.split())}, "
                          f"за {time.time() - started:.0f} с")
                    self._notify(f"готово: {len(text.split())} слов → {target.name}", 12)
                    os.startfile(target)  # человеку нужен текст, а не путь к нему
                except tr.AudioError as exc:
                    self._notify(f"{path.name}: не читается", 10)
                    print(f"⚠️ {path.name}: {exc}")
                except Exception as exc:
                    self._notify(f"{path.name}: ошибка расшифровки", 10)
                    print(f"⚠️ Не смог расшифровать {path.name}: {exc}")
        finally:
            self.busy = False
            self.lock.release()

    def set_save_samples(self, value: bool) -> None:
        """Сохранять ли копии диктовок на диск. Выбор запоминается в .env."""
        import voice_settings as settings

        self.save_samples = value
        settings.write(APP_DIR / ".env", "VOICE_SAVE_SAMPLES", "1" if value else "0")
        self._notify("записи сохраняются" if value else "записи больше не сохраняются", 4)

    # --- управление -------------------------------------------------------

    def toggle(self) -> None:
        """Кнопка или хоткей: работу с микрофоном уводим из потока окна.

        Открытие и остановка звукового устройства могут залипнуть в драйвере
        на минуты. Раньше это делалось прямо в потоке Tk и вешало окно намертво:
        переставали отвечать и кнопка, и хоткей, и крестик. Теперь в худшем
        случае замирает рабочий поток, а окно живо и закрывается.
        """
        threading.Thread(target=self._toggle, daemon=True).start()

    def cancel(self) -> None:
        """Esc: выбросить запись, ничего не распознавая и не вставляя."""
        threading.Thread(target=self._cancel, daemon=True).start()

    def _toggle(self) -> None:
        if self.switching:
            if not self.notice_text():  # не затирать «качаю модель», оно важнее
                self._notify("модель ещё готовится — подожди")
            return
        if self._recognize is None:
            self._notify("модель не загрузилась — выбери другую в меню", 8)
            return
        if not self.lock.acquire(blocking=False):
            self._notify("микрофон не отвечает — жду драйвер")
            return
        try:
            if self.busy:
                return
            if not self.recording:
                self._start()
            else:
                self._stop()
        finally:
            self.lock.release()

    def _cancel(self) -> None:
        if not self.lock.acquire(blocking=False):
            return
        try:
            if not self.recording:
                return
            self._close_stream()
            self.frames = []
            self.levels.clear()
            print("Запись отменена")
            self._notify("запись отменена", 1.5)
            winsound.Beep(300, 100)
        finally:
            self.lock.release()

    # --- запись -----------------------------------------------------------

    def _start(self) -> None:
        import ctypes

        self.target_hwnd = ctypes.windll.user32.GetForegroundWindow()
        self.frames = []
        self.levels.clear()
        print("Открываю микрофон...")  # если следующей строки в логе нет — залип драйвер
        self.stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=self._on_audio
        )
        self.stream.start()
        self.recording = True
        self.started_at = time.time()
        self._bind_esc()
        winsound.Beep(880, 120)
        print("🎙 Запись...")
        self._timer = threading.Timer(MAX_SECONDS, self._auto_stop)
        self._timer.start()

    def _on_audio(self, indata, _frames, _time, _status) -> None:
        chunk = indata[:, 0].copy()
        self.frames.append(chunk)
        rms = float(np.sqrt(np.mean(np.square(chunk)))) if len(chunk) else 0.0
        self.levels.append(min(1.0, rms**0.5 * self.gain))

    def _auto_stop(self) -> None:
        with self.lock:
            if self.recording and time.time() - self.started_at >= MAX_SECONDS:
                print(f"⏱ Авто-стоп после {MAX_SECONDS} с")
                self._stop()

    def _close_stream(self) -> None:
        """Общее для стопа и отмены: закрыть устройство и снять хук Esc."""
        print("Закрываю микрофон...")  # парная метка к «Открываю» — видно, где залипло
        self.stream.stop()
        self.stream.close()
        self.recording = False
        self._unbind_esc()
        if self._timer:
            self._timer.cancel()
            self._timer = None

    def _stop(self) -> None:
        self._close_stream()
        self.busy = True
        winsound.Beep(440, 120)
        audio = np.concatenate(self.frames) if self.frames else np.zeros(0, dtype="float32")
        threading.Thread(target=self._transcribe_and_type, args=(audio,), daemon=True).start()

    # --- Esc ловим только во время записи ---------------------------------

    def _bind_esc(self) -> None:
        import keyboard

        try:
            self._esc_hook = keyboard.on_press_key("esc", lambda _e: self.cancel())
        except Exception as exc:  # без отмены жить можно, без записи — нет
            print(f"⚠️ Не удалось повесить Esc: {exc}")
            self._esc_hook = None

    def _unbind_esc(self) -> None:
        import keyboard

        if self._esc_hook is not None:
            try:
                keyboard.unhook(self._esc_hook)
            except (KeyError, ValueError):
                pass
            self._esc_hook = None

    # --- распознавание и вставка ------------------------------------------

    def _save_sample(self, audio: np.ndarray, text: str) -> None:
        """Кладёт копию записи и её расшифровку в data/dictation.

        Нужно, чтобы потом прогнать другую модель на том же звуке и сравнить
        два текста глазами, а не вспоминать, «как оно было вчера».
        """
        if not self.save_samples or len(audio) < SAMPLE_RATE * MIN_SECONDS:
            return
        DICTATION_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        with wave.open(str(DICTATION_DIR / f"{stamp}.wav"), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)  # int16: float32 из микрофона ужимаем вдвое, на слух не отличить
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes((np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes())
        (DICTATION_DIR / f"{stamp}.txt").write_text(
            f"модель: {self.model_name}\n\n{text}\n", encoding="utf-8"
        )

    def _transcribe_and_type(self, audio: np.ndarray) -> None:
        try:
            if len(audio) < SAMPLE_RATE * MIN_SECONDS:
                self.last_text = "(слишком короткая запись)"
                self._notify("слишком короткая запись")
                print("Слишком короткая запись — пропускаю")
                return
            text = self._recognize(audio)
            if not text:
                self.last_text = "(речь не распознана)"
                self._notify("речь не распознана")
                print("Речь не распознана")
                return
            self.last_text = text
            print(f"→ {text}")
            self._paste(text)
        except Exception as exc:
            self.last_text = f"(ошибка: {exc})"
            self._notify(f"ошибка: {exc}")
            print(f"⚠️ Ошибка распознавания: {exc}")
        finally:
            try:
                self._save_sample(audio, self.last_text)
            except Exception as exc:  # сохранение — побочное дело, диктовку ронять не должно
                print(f"Не сохранил копию записи: {exc}")
            self.busy = False

    def _paste(self, text: str) -> None:
        import ctypes

        import keyboard
        import pyperclip

        # вернуть фокус окну, в котором был курсор на старте записи
        if self.target_hwnd:
            try:
                ctypes.windll.user32.SetForegroundWindow(self.target_hwnd)
                time.sleep(0.15)
            except Exception:
                pass

        previous = None
        try:
            previous = pyperclip.paste()
        except Exception:
            pass
        pyperclip.copy(text)
        time.sleep(0.05)
        keyboard.send("ctrl+v")
        if previous is not None:
            def restore(old=previous):
                time.sleep(1.0)
                try:
                    pyperclip.copy(old)
                except Exception:
                    pass

            threading.Thread(target=restore, daemon=True).start()


# --- иконка в системном лотке ---------------------------------------------

# на светлой панели задач нужна тёмная иконка, на тёмной — светлая
TRAY_ON_LIGHT = {"idle": "#1c1c1e", "recording": "#d70015", "busy": "#0040dd"}
TRAY_ON_DARK = {"idle": "#e8e8ea", "recording": "#ff453a", "busy": "#0a84ff"}


def taskbar_is_light() -> bool:
    """Светлая ли панель задач. Не смогли узнать — считаем светлой: тёмная иконка видна почти везде."""
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            return bool(winreg.QueryValueEx(key, "SystemUsesLightTheme")[0])
    except OSError:
        return True


def tray_colors() -> dict[str, str]:
    return TRAY_ON_LIGHT if taskbar_is_light() else TRAY_ON_DARK


def tray_image(color: str):
    """Микрофон нужного цвета. Штрихи толстые: в лотке картинка сжимается до 16 точек."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([23, 4, 41, 36], radius=9, fill=color)
    d.arc([14, 18, 50, 50], start=0, end=180, fill=color, width=7)
    d.line([32, 47, 32, 58], fill=color, width=7)
    d.line([20, 58, 44, 58], fill=color, width=7)
    return img


def capture_hotkey(recorder: Recorder, hotkey: "Hotkey") -> None:
    """Ждёт нажатие и перевешивает диктовку на эту клавишу. Esc — оставить прежнюю."""
    import keyboard

    recorder.announce("нажми клавишу для диктовки, Esc — отмена", 30)
    while True:
        event = keyboard.read_event()
        if event.event_type == "down" and event.name:
            break
    if event.name == "esc":
        recorder.announce("оставил прежнюю клавишу", 3)
        return
    hotkey.rebind(event.name)
    recorder.announce(f"диктовка теперь на {event.name.upper()}", 4)


def start_tray(recorder: Recorder, quit_event: threading.Event, hotkey: "Hotkey",
               ask_for_file=None):
    """Иконка в лотке: клик — запись, правая кнопка — настройки. Живёт в своём потоке."""
    import pystray

    def in_background(work):
        """Меню не должно ждать: смена модели качает сотни мегабайт, перехват клавиши — ждёт нажатия.

        После работы обязательно пересобираем меню. Windows строит его один раз и
        кеширует, поэтому подписи и галочки внутри сами не обновляются: без этого
        вызова пункт «Горячая клавиша» показывал бы прежнюю клавишу до перезапуска.
        """
        def run():
            try:
                work()
            finally:
                icon.update_menu()

        threading.Thread(target=run, daemon=True).start()

    def on_quit(icon, _item=None):
        quit_event.set()
        icon.visible = False  # иначе Windows держит «призрак» иконки до наведения мышью
        icon.stop()

    def on_about(icon, _item=None):
        text = (
            f"{hotkey.key.upper()} — начать и остановить диктовку, Esc — отменить.\n"
            f"Модель: {ASR_MODELS.get(recorder.asr_model, recorder.model_name)}.\n"
            "Речь обрабатывается на этом компьютере и никуда не отправляется.\n"
            f"Автор: {APP_AUTHOR}. Открытый код, лицензия MIT: {APP_URL}"
        )
        try:
            icon.notify(text, f"{APP_NAME} {APP_VERSION} — голосовая диктовка")
        except Exception:  # всплывающие подсказки есть не в каждой системе
            recorder.announce(f"{APP_NAME}: {recorder.model_name}, клавиша {hotkey.key.upper()}", 6)

    def choose_model(name: str):
        return lambda icon, _item=None: in_background(lambda: recorder.switch_model(name))

    model_items = [
        pystray.MenuItem(
            label,
            choose_model(name),
            checked=(lambda n: lambda _item: recorder.asr_model == n)(name),
            radio=True,
        )
        for name, label in ASR_MODELS.items()
    ]

    icon = pystray.Icon(
        APP_NAME.lower(),
        tray_image(tray_colors()["idle"]),
        APP_TAGLINE,
        menu=pystray.Menu(
            pystray.MenuItem("Начать / остановить запись", lambda *_: recorder.toggle(),
                             default=True),
            pystray.MenuItem("Расшифровать аудиофайл…", lambda *_: ask_for_file(),
                             visible=ask_for_file is not None),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Язык и модель", pystray.Menu(*model_items)),
            pystray.MenuItem(
                "Сохранять записи на диск",
                lambda *_: in_background(
                    lambda: recorder.set_save_samples(not recorder.save_samples)
                ),
                checked=lambda _item: recorder.save_samples,
            ),
            pystray.MenuItem(
                lambda _item: f"Горячая клавиша: {hotkey.key.upper()}",
                lambda *_: in_background(lambda: capture_hotkey(recorder, hotkey)),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("О программе", on_about),
            pystray.MenuItem("Выход", on_quit),
        ),
    )

    def follow_state():
        shown = "idle"
        while not quit_event.wait(0.2):
            if recorder.state != shown:
                shown = recorder.state
                icon.icon = tray_image(tray_colors()[shown])

    threading.Thread(target=follow_state, daemon=True).start()
    threading.Thread(target=icon.run, daemon=True).start()
    return icon


def check(asr_model: str = DEFAULT_ASR_MODEL) -> int:
    try:
        default_input = sd.query_devices(kind="input")
        print(f"✅ Микрофон: {default_input['name']}")
    except Exception as exc:
        print(f"❌ Микрофон не найден: {exc}")
        return 1

    started = time.time()
    name, _recognize, _model = load_engine(asr_model)
    print(f"✅ {name} загружается за {time.time() - started:.1f} с")
    print("✅ Всё готово: запускай без --check")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=f"{APP_TAGLINE}, локальная")
    parser.add_argument("--version", action="version",
                        version=f"{APP_NAME} {APP_VERSION} — {APP_URL}")
    parser.add_argument("--check", action="store_true", help="проверить микрофон и модель, выйти")
    parser.add_argument("--headless", action="store_true", help="без окна, только хоткей")
    parser.add_argument("files", nargs="*", help="аудиофайлы для расшифровки в текст")
    args = parser.parse_args()

    # первой строкой в журнале — что именно запущено: без этого чужой лог не разобрать
    print(f"{APP_NAME} {APP_VERSION}, папка программы: {APP_DIR}")

    load_dotenv(APP_DIR / ".env")
    asr_model = os.getenv("ASR_MODEL", DEFAULT_ASR_MODEL)
    if asr_model not in ASR_MODELS:
        print(f"Модель {asr_model} не из списка проверенных — пробую, но может не подойти")
    hotkey_key = os.getenv("VOICE_HOTKEY", "f8")
    gain = float(os.getenv("VOICE_GAIN", "2.2"))

    import voice_settings as settings

    # По умолчанию не сохраняем: модели уже выбраны замерами, а копии диктовок
    # копятся мегабайтами. Понадобится сравнить новую модель — включить в меню.
    save_samples = settings.as_bool(os.getenv("VOICE_SAVE_SAMPLES"), default=False)

    if args.check:
        sys.exit(check(asr_model))

    import keyboard

    # Если программа уже работает, этот вызов не вернётся: файлы уйдут ей, а мы выйдем
    lock = ensure_single_instance(args.files)
    recorder = Recorder(gain, asr_model, save_samples)
    hotkey = Hotkey(hotkey_key, recorder.toggle)
    listen_for_files(lock, recorder.transcribe_files)

    if args.headless:
        recorder.load()
        if args.files:
            recorder.transcribe_files(args.files)
        print(f"Готов (headless). Хоткей: {hotkey.key}")
        keyboard.wait()
        return

    from voice_window import VoiceWindow

    window = VoiceWindow(recorder, hotkey.key, on_files=recorder.transcribe_files)
    hotkey.on_change = window.set_hotkey
    start_tray(recorder, window.should_quit, hotkey, ask_for_file=window.ask_for_file)
    print(f"Окно и значок на месте. Хоткей: {hotkey.key}, "
          f"стекло: {'да' if window.glass else 'нет (матовый фон)'}")

    def prepare():
        """Модель грузим последней и фоном: окно уже нарисовано, значит виден ход дела."""
        recorder.load()
        if args.files:  # файлы бросили на программу, когда она ещё не работала
            recorder.transcribe_files(args.files)

    threading.Thread(target=prepare, daemon=True).start()
    window.run()
    print("Выход по команде из лотка")


if __name__ == "__main__":
    main()
