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
import contextlib
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

import replacements
import messages
from messages import t


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
# папки в models/. Значение — ключ подписи, а не сама подпись: в казахском меню
# названия моделей оставались русскими, потому что лежали здесь строками.
# Подписи короткие: подробности про языки и знаки препинания живут в справке,
# а три строки в разной манере не давали понять, чем модели различаются.
ASR_MODELS = {
    "gigaam-v3-e2e-rnnt": "menu.model_russian",
    "gigaam-multilingual-ctc": "menu.model_multi",
    "gigaam-multilingual-large-ctc": "menu.model_multi_large",
}
# Сколько качать, если весов ещё нет. Человеку нужно знать это до нажатия,
# а не после: четверть гигабайта на рабочем интернете — уже решение.
MODEL_SIZES = {
    "gigaam-v3-e2e-rnnt": "216 МБ",
    "gigaam-multilingual-ctc": "225 МБ",
    "gigaam-multilingual-large-ctc": "592 МБ",
}
DEFAULT_ASR_MODEL = "gigaam-v3-e2e-rnnt"
# Знаки препинания ставит только многоязычная ветка: у русской e2e-модели они
# свои, и трогать их второй раз нельзя — испортим то, что и так верно.
PUNCTUATED_BY_MODEL = {"gigaam-v3-e2e-rnnt"}
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


def module_ready(name: str) -> bool:
    """Лежат ли веса модуля рядом с программой.

    Смотрим на файлы, а не на папку: оборванная закачка оставляет пустую папку,
    и по её наличию модуль выглядел бы готовым.
    """
    folder = APP_DIR / "models" / name
    return folder.exists() and any(folder.glob("*.onnx"))


def read_minutes(value: str | None) -> int:
    """Предел записи из настройки. Мусор и число не из списка — умолчание.

    Значение правят и руками в .env. «5 минут», пустая строка, «сто» — всё это
    не повод не запуститься: непонятное молча заменяется умолчанием, как и
    незнакомый код языка. Испорченная настройка не должна ломать программу.
    """
    try:
        minutes = int(str(value).strip())
    except (TypeError, ValueError):
        return DEFAULT_MINUTES
    return minutes if minutes in RECORD_MINUTES else DEFAULT_MINUTES


def model_label(name: str, key: str) -> str:
    """Подпись пункта меню. Весов нет — говорим, сколько качать."""
    label = t(key)
    if module_ready(name):
        return label
    return t("menu.model_download", label=label,
             size=MODEL_SIZES.get(name, "")).rstrip(" —")


APP_NAME = "Dictum"
# Три числа: ломающее изменение . новые возможности . исправления.
# Единственное место, где версия записана: отсюда её берут «О программе», журнал
# и свойства exe, которые показывает проводник Windows.
APP_VERSION = "1.3.0"
APP_TAGLINE = f"{APP_NAME} — голосовая диктовка"
APP_AUTHOR = "Gshin-droid"
APP_URL = "github.com/Gshin-droid/dictum"

SAMPLE_RATE = 16000
# Предохранитель на случай «забыл выключить»: дольше выбранного программа не
# пишет и останавливается сама. Технических доводов за конкретное число больше
# нет — нарезка по паузам убрала и рост времени, и потерю текста на длинной
# записи. Осталась одна причина: сколько человек за раз наговаривает. Поэтому
# выбор отдан ему, а не зашит числом.
RECORD_MINUTES = (2, 5, 10)
DEFAULT_MINUTES = 2
MIN_SECONDS = 0.3

# Длинную диктовку режем по паузам и распознаём куски, пока человек ещё говорит.
# Причин три, и ускорение среди них не первая.
#   1. Модель обучена на кусках до полуминуты и на длинной записи молча
#      теряет текст. В расшифровке файлов это уже закрыто нарезкой: замер на
#      трёхминутной записи дал 164 слова без нарезки и 275 с нарезкой.
#   2. У многоязычной модели время растёт быстрее длины: 5 с записи стоят 0,37 с,
#      60 с — уже 7,5 с, 120 с — 26 с. Восемь кусков по 15 с обойдутся в 8,8 с.
#   3. Когда куски считаются во время записи, после клавиши остаётся один.
CHUNK_MIN_SECONDS = 6.0   # короче не режем: короткую диктовку трогать незачем
CHUNK_MAX_SECONDS = 20.0  # дольше не копим, даже если человек не делает пауз
# Хвост после последнего разреза — не то же самое, что вся запись, и порог ему
# нужен свой. MIN_SECONDS = 0,3 с отвечает на вопрос «не задел ли человек
# клавишу случайно»; хвосту тот же порог стоил бы последнего слова: сказал
# «...решение. Да», разрез прошёл по паузе перед «да» — и четверть секунды речи
# молча выброшена. У Handy это открытый дефект #1983 «пропадает последнее слово».
# Пустой ответ модели отсеется сам, а вот выброшенное слово не вернуть.
TAIL_MIN_SECONDS = 0.1
# «Звука не было вовсе» — не «сказал тихо»: устройством ввода оказался не
# микрофон, штекер не воткнут либо вход отключён. Порог стоял у самого нуля
# (1e-4) и на живом отказе не сработал: мёртвый вход отдаёт не ровный ноль, а
# собственный шум усилителя.
#
# Считаем не по пику, а по средней громкости (СКО). Пик решает плохо: у мёртвого
# входа он доходил до 0,0038, а у живой комнаты в тишине опускался до 0,0097 —
# полтора раза разницы, любой щелчок мышью их меняет местами. По СКО те же
# записи расходятся в тридцать раз. Замерено на этой машине:
#
#     мёртвый вход, 4 с        СКО 0,0002   пик 0,0009
#     тишина комнаты, 20 с     СКО 0,0062   пик 0,0153
#     живая диктовка, 51 с     СКО 0,0219   пик 0,8954
#     самая тихая её секунда   СКО 0,0100   пик 0,0207
#
# Порог 0,002 стоит между мёртвым входом и тишиной живой комнаты с запасом в
# пять раз в обе стороны. Цена ошибки — только выбор надписи: сюда попадают лишь
# записи, из которых распознавание и так не достало ни слова.
SILENT_RMS = 0.002
PAUSE_SECONDS = 0.5       # пауза, по которой отрезаем
# Тишину считаем долей от громкости самого куска, а не по постоянному порогу: у
# одного микрофон шепчет, у другого шумит, и одно число подошло бы только одному.
# Постоянный нижний порог тут стоял и был ошибкой: 0,003 оказалось ГРОМЧЕ тихой
# речи, и на тихом микрофоне тишиной объявлялась сама речь — разрез приходился
# на середину слова. Поймала проверка, а не глаза.
SILENCE_SHARE = 0.08
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


LOG_LIMIT = 1_000_000  # больше мегабайта журнал не нужен никому


def log_path() -> Path:
    """Куда писать журнал. Рядом с программой, а если туда нельзя — во временную папку.

    Переносную копию распакуют куда угодно, в том числе в Program Files, где
    обычной программе писать запрещено. Молча умереть на этой строке нельзя:
    консоли нет, и человек не увидит ни ошибки, ни программы — просто ничего.
    """
    try:
        (APP_DIR / "logs").mkdir(exist_ok=True)
        target = APP_DIR / "logs" / "dictum.log"
        target.touch(exist_ok=True)
        return target
    except OSError:
        import tempfile

        return Path(tempfile.gettempdir()) / "dictum.log"


def open_log():
    """Открывает журнал, отложив в сторону разросшийся: он не должен расти вечно."""
    target = log_path()
    try:
        if target.exists() and target.stat().st_size > LOG_LIMIT:
            target.replace(target.with_suffix(".old.log"))
    except OSError:
        pass  # не вышло подвинуть — пишем дальше в тот же, это не повод падать
    try:
        return open(target, "a", encoding="utf-8", buffering=1)
    except OSError:
        import tempfile

        return open(Path(tempfile.gettempdir()) / "dictum.log", "a", encoding="utf-8", buffering=1)


def show_error(text: str) -> None:
    """Окно с ошибкой. В сборке без консоли это единственный способ что-то сказать.

    Человеку, которому программу отдали, журнал не поможет, если он не знает,
    что журнал есть. Поэтому про поломку говорим прямо на экране и называем файл,
    который надо прислать.
    """
    import ctypes

    try:
        ctypes.windll.user32.MessageBoxW(
            0, text, f"{APP_NAME}: не удалось запуститься", 0x10 | 0x1000
        )
    except Exception as exc:  # окна не показать — хотя бы в журнал
        print(f"Не смог показать окно с ошибкой: {exc}")


def report_crash(kind, value, trace) -> None:
    """Ловушка для ошибок, которые никто не поймал: и в главном потоке, и в рабочих.

    Без неё сборка без консоли просто исчезает с экрана: traceback печатается в
    поток, которого нет, и человек видит, что программа «не запустилась». Именно
    так и вышло у первого же получателя.
    """
    import traceback

    text = "".join(traceback.format_exception(kind, value, trace)).strip()
    print(f"⚠️ НЕОБРАБОТАННАЯ ОШИБКА\n{text}\n--- конец ошибки ---")
    show_error(
        f"{kind.__name__}: {value}\n\n"
        f"Что случилось — записано в файл:\n{log_path()}\n\n"
        "Пришли этот файл автору, по нему видно причину."
    )


# Журнал и ловушка ошибок ставятся, только когда voice_input запущен как
# программа, — при импорте не ставятся. Сборщик и тесты импортируют его ради
# пары констант, и получать в нагрузку чужой журнал вместо своего вывода и
# модальное окно вместо трассировки им незачем. Однажды это уже стоило десяти
# минут: ошибка в build_exe.py показала окно и заморозила сборку до клика по
# нему, а весь вывод сборки ушёл в журнал программы.
if __name__ == "__main__":
    # В сборке PyInstaller без консоли sys.stdout не пустой, а заглушка, молча
    # глотающая вывод, — поэтому проверяем признак сборки, а не только пустоту.
    if getattr(sys, "frozen", False) or sys.stdout is None or sys.stderr is None:
        sys.stdout = sys.stderr = _Stamped(open_log())
    else:
        # консоль Windows живёт в cp1251: без этого любой ✅ в выводе роняет процесс
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    sys.excepthook = report_crash
    threading.excepthook = lambda args: report_crash(
        args.exc_type, args.exc_value, args.exc_traceback
    )


def describe_environment() -> str:
    """Что писать в журнал первым делом. Без этого чужой журнал не разобрать.

    Всё здесь — то, обо что программа спотыкалась или может споткнуться: не та
    папка, нет места под модель, модель не докачалась, не та разрядность системы.
    """
    import platform

    lines = [
        f"{APP_NAME} {APP_VERSION}",
        f"папка программы: {APP_DIR}",
        f"сборка: {'exe' if getattr(sys, 'frozen', False) else 'запуск из исходников'}",
        f"система: {platform.platform()}, python {platform.python_version()}",
        f"журнал: {log_path()}",
    ]
    try:
        lines.append(f"свободно на диске: {shutil.disk_usage(APP_DIR).free / 1e9:.1f} ГБ")
    except OSError as exc:
        lines.append(f"место на диске не определилось: {exc}")

    models = APP_DIR / "models"
    found = sorted(p.name for p in models.iterdir() if p.is_dir()) if models.exists() else []
    lines.append(f"модели на месте: {', '.join(found) if found else 'нет, будут скачаны'}")
    return "\n".join(lines)


def trust_windows_certificates() -> None:
    """Пускает качалку моделей в хранилище сертификатов Windows.

    Антивирусы с проверкой защищённых соединений — Avast, Kaspersky, ESET, а
    ещё рабочие прокси — вскрывают трафик и подписывают его своим сертификатом.
    Windows ему доверяет, браузер работает, а качалка моделей падает: она
    смотрит только в свой список `certifi` и системное хранилище не спрашивает.
    Человек при этом видит «не удалось скачать модель» на исправной сети.

    Доверия не убавляется и не прибавляется сверх того, что уже есть в системе:
    к списку `certifi` добавляются корневые сертификаты самой Windows — те, по
    которым тут работает всякая другая программа.
    """
    import ssl
    import tempfile

    if os.environ.get("SSL_CERT_FILE"):
        return  # человек или окружение уже назначили свой список — не спорим
    try:
        import certifi

        parts = [Path(certifi.where()).read_bytes()]
        parts += [ssl.DER_cert_to_PEM_cert(cert).encode()
                  for cert, _, _ in ssl.enum_certificates("ROOT")]
        bundle = Path(tempfile.gettempdir()) / "dictum-ca.pem"
        bundle.write_bytes(b"".join(parts))
        os.environ["SSL_CERT_FILE"] = str(bundle)
    except Exception as exc:  # список не собрался — качаем как раньше
        print(f"Сертификаты Windows не подхватились ({exc}) — скачивание может "
              f"не пройти через антивирус с проверкой соединений")


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
        return sock  # держим сокет открытым до конца работы процесса
    except OSError as exc:
        print(f"Порт {SINGLE_INSTANCE_PORT} занять не удалось "
              f"(код {exc.errno}: {exc.strerror or exc})")

    # Занятый порт — ещё НЕ доказательство, что работает наша программа. Его мог
    # взять кто угодно, а после недавнего выхода Windows держит его занятым ещё
    # пару минут. Раньше мы на этом сдавались и писали «уже запущен» — человек
    # видел, что программа не открылась, и причины узнать не мог.
    # Отказ от запуска требует доказательства: на том конце должны ответить.
    if talk_to_running_copy(files):
        sys.exit(0)

    print("⚠️ На том конце никто не ответил — значит это не наша копия, а чужая "
          "программа или след недавнего выхода. Запускаюсь без замка.")
    print("   Последствие: перетаскивание файлов на программу работать не будет, "
          "расшифровка — только через меню значка.")
    sock.close()
    return None


def talk_to_running_copy(files: list[str] | None) -> bool:
    """Стучимся в занятый порт. Ответили — там наша копия, ей и отдаём файлы."""
    import socket

    try:
        with socket.create_connection(("127.0.0.1", SINGLE_INSTANCE_PORT), timeout=3) as out:
            if files:
                out.sendall("\n".join(files).encode("utf-8"))
                print(f"Передал уже запущенной программе файлов: {len(files)}")
            else:
                print("Программа уже запущена — этот запуск закрываю")
        return True
    except OSError as exc:
        print(f"Достучаться до занятого порта не вышло: {exc}")
        return False


def listen_for_files(sock, handle) -> None:
    """Принимает пути от второго экземпляра. Свой поток, чтобы не держать окно."""
    if sock is None:  # замок взять не удалось, слушать нечего
        return

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


def clipboard_holds(text: str, tries: int = 10, pause: float = 0.05) -> bool:
    """Правда ли, что в буфере обмена лежит ровно наш текст.

    Ждём не время, а свидетельство. Слепая пауза перед Ctrl+V — это ставка на то,
    что буфер успел обновиться; под нагрузкой ставка не играет, и в чужое окно
    уходит прежнее содержимое буфера вместо диктовки. У Handy это открытый
    дефект #502, лечат его там наращиванием паузы — то есть той же ставкой,
    только крупнее.

    Первая попытка обычно и последняя: `copy()` в Windows возвращается уже после
    записи. Повторы нужны для менеджеров буфера (Win+V, Ditto, Punto Switcher):
    они отбирают владение и отдают обратно не мгновенно.

    Перевод строки Windows хранит как \\r\\n, поэтому сравниваем нормализованно.
    """
    import pyperclip

    for _ in range(tries):
        try:
            if (pyperclip.paste() or "").replace("\r\n", "\n") == text:
                return True
        except Exception:
            pass  # буфер под замком у чужой программы — подождём и спросим ещё раз
        time.sleep(pause)
    return False


class Recorder:
    """Запись, распознавание и вставка текста. Окно опрашивает состояние снаружи."""

    def __init__(self, gain: float = 2.2, asr_model: str = DEFAULT_ASR_MODEL,
                 save_samples: bool = False, max_minutes: int = DEFAULT_MINUTES):
        self.asr_model = asr_model
        self.save_samples = save_samples
        self.model_name = asr_model
        self._recognize = None  # модель ещё не загружена
        self._model = None  # сама модель: нужна расшифровке файлов
        self._vad = None  # нарезчик по тишине, грузится по первой надобности
        self._punctuator = None  # знаки препинания, грузятся по первой надобности
        self._paste_hooks = []  # слежение за ручной вставкой, см. _watch_for_paste
        self._dictionary = replacements.Dictionary(APP_DIR / replacements.FILE_NAME)
        self.punctuate = True  # выключатель в меню, значение приходит из .env
        self.max_minutes = max_minutes  # предел одной записи, тоже из меню
        self.switching = True  # грузится или меняется модель: запись пока не начинаем
        self.gain = gain  # чувствительность волны: подкрутить, если полоски вялые или зашкаливают
        self.recording = False
        self.busy = False
        self.last_text = ""
        self.target_hwnd = 0  # окно, куда вставлять текст (фокус на момент старта записи)
        self.levels: deque[float] = deque(maxlen=LEVELS_KEPT)
        self.frames: list[np.ndarray] = []
        # Громкость каждого блока без подкрутки чувствительности и без обрезки по
        # длине: levels живёт окном на 200 блоков и годится только для волны,
        # а разрез ищется по всей записи и не должен зависеть от настройки gain.
        self._rms: list[float] = []
        self._parts: list[str] = []  # распознанные куски, по порядку
        self._taken = 0              # сколько блоков уже ушло в куски
        self._take = 0               # номер записи: отменённая не должна дописаться
        self._worker: threading.Thread | None = None
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

    @contextlib.contextmanager
    def _working(self, text: str):
        """Показывает сообщение ровно столько, сколько идёт работа, и ни секундой больше.

        Долгое дело нельзя объявлять обычным сообщением с запасом по времени:
        срок переживает саму работу, капсула остаётся на экране, и со стороны это
        выглядит зависанием. Так и случилось у первого проверяющего — текст уже
        был вставлен, а «готовлю знаки препинания…» висело ещё минуту.

        Снятие в finally, а не в конце блока: упавшая работа тем более не повод
        оставлять окно на экране.
        """
        self._notify(text, 3600)  # срок только предохранитель, снимаем сами
        try:
            yield
        finally:
            self._notify("")

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
            self._notify(t("notice.first_run"), 3600)
        try:
            self.model_name, self._recognize, self._model = load_engine(self.asr_model)
            self._notify(t("notice.ready"), 4)
            print(f"Готов. Модель: {self.model_name}")
        except Exception as exc:
            self._notify(t("notice.model_failed"), 600)
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
            self._notify(t("notice.model_loading"))
            return False
        if self.recording or self.busy:
            self._notify(t("notice.finish_first"))
            return False

        previous = (self.model_name, self._recognize, self._model, self.asr_model)
        self.switching = True
        self._notify(t("notice.model_preparing", name=name), 900)
        try:
            self.model_name, self._recognize, self._model = load_engine(name)
            self.asr_model = name
            settings.write(APP_DIR / ".env", "ASR_MODEL", name)
            self._notify(t("notice.model_ready"), 4)
            print(f"Модель переключена на {name}")
            return True
        except Exception as exc:
            self.model_name, self._recognize, self._model, self.asr_model = previous
            self._notify(t("notice.model_switch_failed"), 6)
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
            self._notify(t("notice.model_loading"), 6)
            return
        if not self.lock.acquire(blocking=False):  # идёт диктовка — не мешаем ей
            self._notify(t("notice.finish_first"), 6)
            return
        try:
            self.busy = True  # окно покажет «занято», а клавиша не начнёт запись
            if self._vad is None:
                with self._working(t("notice.preparing_vad")):
                    self._vad = load_vad()
            for number, name in enumerate(paths, 1):
                path = Path(name)
                counter = f"{number} из {len(paths)}: " if len(paths) > 1 else ""
                try:
                    self._notify(t("notice.reading_file", counter=counter, name=path.name), 300)
                    audio = tr.read_audio(path)
                    seconds = len(audio) / SAMPLE_RATE
                    print(f"Расшифровываю {path.name}: {seconds / 60:.1f} мин")

                    def show(done, counter=counter, seconds=seconds):
                        self._notify(t("notice.transcribing", counter=counter,
                                      percent=f"{done * 100:.0f}",
                                      minutes=f"{seconds / 60:.0f}"), 300)

                    started = time.time()
                    text = tr.transcribe(audio, self._model, self._vad, show,
                                         polish=self._polish)
                    if not text:
                        self._notify(t("notice.file_no_speech", name=path.name), 8)
                        print(f"В {path.name} речи не нашлось")
                        continue
                    target = tr.save(path, text, self.model_name, seconds)
                    print(f"→ {target.name}: слов {len(text.split())}, "
                          f"за {time.time() - started:.0f} с")
                    self._notify(t("notice.file_done", words=len(text.split()), name=target.name), 12)
                    os.startfile(target)  # человеку нужен текст, а не путь к нему
                except tr.AudioError as exc:
                    self._notify(t("notice.file_unreadable", name=path.name), 10)
                    print(f"⚠️ {path.name}: {exc}")
                except Exception as exc:
                    self._notify(t("notice.file_failed", name=path.name), 10)
                    print(f"⚠️ Не смог расшифровать {path.name}: {exc}")
        finally:
            self.busy = False
            self.lock.release()

    def set_save_samples(self, value: bool) -> None:
        """Сохранять ли копии диктовок на диск. Выбор запоминается в .env."""
        import voice_settings as settings

        self.save_samples = value
        settings.write(APP_DIR / ".env", "VOICE_SAVE_SAMPLES", "1" if value else "0")
        self._notify(t("notice.samples_on" if value else "notice.samples_off"), 4)

    def set_punctuate(self, value: bool) -> None:
        """Ставить ли знаки препинания. Выбор запоминается в .env."""
        import voice_settings as settings

        self.punctuate = value
        settings.write(APP_DIR / ".env", "VOICE_PUNCTUATE", "1" if value else "0")
        self._notify(t("notice.punct_on" if value else "notice.punct_off"), 4)

    def set_max_minutes(self, minutes: int) -> None:
        """Предел одной записи. Выбор запоминается в .env.

        Новый предел вступает в силу со следующей записи: таймер заводится в
        момент нажатия клавиши, и трогать уже идущую запись нельзя — человек
        рассчитывал на прежний срок.
        """
        import voice_settings as settings

        self.max_minutes = minutes
        settings.write(APP_DIR / ".env", "VOICE_MAX_MINUTES", str(minutes))
        self._notify(t("notice.record_length", minutes=minutes), 4)

    def set_interface_language(self, code: str) -> None:
        """Язык надписей на экране. Выбор запоминается в .env.

        Сообщение о смене показываем уже на новом языке — это единственная
        проверка, доступная человеку сразу: увидел знакомое слово — язык встал.
        """
        import voice_settings as settings

        messages.set_language(code)
        settings.write(APP_DIR / ".env", "VOICE_LANG", messages.language())
        self._notify(t("notice.language_set",
                       language=messages.LANGUAGES.get(messages.language(), code)), 4)

    def copy_last_text(self) -> None:
        """Кладёт последнюю диктовку в буфер обмена.

        Страховка на то, чего автоматика не ловит: вставку правой кнопкой мыши
        и случай «окно то, а курсор не в текстовом поле» — про второй Windows
        честно не скажет ничего для Chrome и всего, что на Electron.
        """
        import pyperclip

        if not self.last_text:
            self._notify(t("notice.nothing_yet"))
            return
        try:
            pyperclip.copy(self.last_text)
            self._notify(t("notice.last_copied"), 4)
        except Exception as exc:
            print(f"⚠️ Не смог положить текст в буфер: {exc}")
            self._notify(t("notice.clipboard_taken"), 6)

    def _polish(self, text: str) -> str:
        """Довести распознанное до вида, в котором его вставляют.

        Два шага, и второй нужен обеим моделям: русская пишет «Google» верно,
        но имён и терминов человека не знает так же, как многоязычная.
        """
        return self._replace(self._punctuation(text))

    def _replace(self, text: str) -> str:
        """Словарь замен. Сломанный файл не имеет права уронить диктовку."""
        try:
            return self._dictionary.apply(text)
        except Exception as exc:
            print(f"⚠️ Словарь замен не применился: {exc}")
            return text

    def _punctuation(self, text: str) -> str:
        """Знаки препинания, если они уместны. Не вышло — отдаём как было.

        Сбой пунктуатора не имеет права уронить диктовку: текст уже распознан,
        и отдать его без запятых куда лучше, чем не отдать вовсе.
        """
        if not self.punctuate or self.asr_model in PUNCTUATED_BY_MODEL:
            return text
        try:
            if self._punctuator is None:
                import punctuate

                # Закачка идёт две минуты, и всё это время капсула показывала
                # «готовлю знаки препинания…». Со стороны это ровно то же, что
                # зависание: человек не знает, ждать ему или убивать программу.
                папка = APP_DIR / "models"
                качаем = punctuate.missing(папка)
                доля = (lambda часть: self._notify(
                    t("notice.downloading_punct", percent=int(часть * 100)), 3600)
                ) if качаем else None
                надпись = "notice.downloading_punct" if качаем else "notice.preparing_punct"
                with self._working(t(надпись, percent=0)):
                    self._punctuator = punctuate.load(папка, доля)
            return self._punctuator.apply(text)
        except Exception as exc:
            print(f"⚠️ Знаки препинания не поставились: {exc}")
            return text

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
                self._notify(t("notice.model_loading"))
            return
        if self._recognize is None:
            self._notify(t("notice.model_failed"), 8)
            return
        if not self.lock.acquire(blocking=False):
            self._notify(t("notice.mic_busy"))
            return
        try:
            if self.busy:
                return
            if not self.recording:
                # Отказ микрофона не имеет права уронить программу: раньше
                # исключение уходило наверх, поток управления умирал вместе с
                # ним, и клавиша молчала до перезапуска — при живом значке в
                # трее. Одна несостоявшаяся диктовка дешевле мёртвой программы.
                try:
                    self._start()
                except Exception as exc:
                    self.recording = False
                    print(f"⚠️ Микрофон не открылся: {exc}")
                    self._notify(t("notice.mic_failed"), 8)
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
            self._take += 1  # номер сменился: кусок, что сейчас в работе, не допишется
            self.frames = []
            self.levels.clear()
            self._rms = []
            self._parts = []
            self._taken = 0
            print("Запись отменена")
            self._notify(t("notice.cancelled"), 1.5)
            winsound.Beep(300, 100)
        finally:
            self.lock.release()

    # --- запись -----------------------------------------------------------

    def _start(self) -> None:
        import ctypes

        # Обещание вернуть буфер от прошлой диктовки к этому моменту протухло:
        # человек её так и не вставил, а новый текст сейчас займёт буфер сам.
        self._forget_paste_watch()
        self.target_hwnd = ctypes.windll.user32.GetForegroundWindow()
        self.frames = []
        self.levels.clear()
        self._rms = []
        self._parts = []
        self._taken = 0
        self._take += 1
        print("Открываю микрофон...")  # если следующей строки в логе нет — залип драйвер
        self.stream = self._open_stream()
        self.stream.start()
        self.recording = True
        self.started_at = time.time()
        self._bind_esc()
        winsound.Beep(880, 120)
        print("🎙 Запись...")
        self._worker = threading.Thread(target=self._stream_worker, daemon=True)
        self._worker.start()
        self._timer = threading.Timer(self.max_minutes * 60, self._auto_stop)
        self._timer.start()

    def _open_stream(self):
        """Открывает микрофон, перечитав список устройств, если он протух.

        PortAudio перечисляет устройства ОДИН раз — когда загружается
        sounddevice. Программа живёт в трее сутками, и за это время список
        меняется: конференция забирает микрофон, подключается гарнитура,
        Windows переназначает номера устройств MME. Запомненный номер тогда
        указывает в пустоту, и открытие падает с «a device ID has been used
        that is out of range». Пришло из отчёта о поломке: программа
        проработала сутки, человек подключился к конференции — и диктовка
        умерла до перезапуска.

        Перечитать список можно только скрытыми _terminate/_initialize. Средство
        не самодеятельное: его называет сам сопровождающий библиотеки в
        python-sounddevice#3, в открытый интерфейс их так и не вынесли.
        """
        def открыть():
            return sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                  dtype="float32", callback=self._on_audio)

        try:
            return открыть()
        except sd.PortAudioError as exc:
            print(f"Микрофон не открылся ({exc}) — перечитываю список устройств")
            sd._terminate()
            sd._initialize()
            return открыть()  # не помогло — пусть падает наверх, там об этом скажут

    def _on_audio(self, indata, _frames, _time, _status) -> None:
        chunk = indata[:, 0].copy()
        self.frames.append(chunk)
        rms = float(np.sqrt(np.mean(np.square(chunk)))) if len(chunk) else 0.0
        # порядок важен: разрезчик берёт min(len(frames), len(_rms)) и потому
        # никогда не заглянет в блок, для которого громкость ещё не посчитана
        self._rms.append(rms)
        self.levels.append(min(1.0, rms**0.5 * self.gain))

    # --- нарезка по паузам во время записи --------------------------------

    def find_cut(self, taken: int) -> int:
        """По какой блок можно отрезать готовый кусок. Ноль — резать рано.

        Ищем паузу в полсекунды не раньше, чем через CHUNK_MIN_SECONDS от начала
        куска. Не нашли, а кусок дорос до CHUNK_MAX_SECONDS — режем по самому
        тихому месту: человек может говорить без пауз, но обрывать его на
        случайном блоке хуже, чем на самом тихом за последние секунды.

        Ничего не нашли — возвращаем ноль, и программа работает как раньше:
        весь звук уйдёт в распознавание одним куском. Это важное свойство —
        неудачная нарезка ничего не ломает, а просто не случается.
        """
        frames, rms = self.frames, self._rms
        total = min(len(frames), len(rms))
        if total <= taken:
            return 0
        seconds = np.cumsum([len(frames[i]) for i in range(taken, total)]) / SAMPLE_RATE
        if seconds[-1] < CHUNK_MIN_SECONDS:
            return 0

        window = rms[taken:total]
        quiet = max(window) * SILENCE_SHARE
        first = int(np.searchsorted(seconds, CHUNK_MIN_SECONDS))
        run = 0
        for i in range(first, len(window)):
            if window[i] >= quiet:
                run = 0
                continue
            run += 1
            начало = max(0, i - run)
            if seconds[i] - seconds[начало] >= PAUSE_SECONDS:
                return taken + i + 1
        if seconds[-1] >= CHUNK_MAX_SECONDS:
            return taken + first + int(np.argmin(window[first:])) + 1
        return 0

    def _stream_worker(self) -> None:
        """Распознаёт готовые куски, пока человек ещё говорит.

        Живёт в своём потоке и только читает: звук ему кладёт поток микрофона,
        а сам он ничего не удаляет — полная запись остаётся целой для сохранения
        копии и для проверки «слишком короткая».
        """
        take, taken = self._take, 0
        while True:
            cut = self.find_cut(taken) if self._recognize else 0
            if cut:
                audio = np.concatenate(self.frames[taken:cut])
                taken = cut
                try:
                    text = self._recognize(audio).strip()
                except Exception as exc:  # кусок пропал, диктовка — нет
                    print(f"⚠️ Кусок не распознан: {exc}")
                    text = ""
                if take != self._take:  # запись отменили, дописывать некуда
                    return
                self._taken = taken
                if text:
                    self._parts.append(text)
                    print(f"→ кусок: {text}")
                continue
            if not self.recording:
                self._taken = taken
                return
            time.sleep(0.15)

    def preview_text(self) -> str | None:
        """Что уже распознано, пока человек ещё говорит. Показывает капсула."""
        if not self.recording and not self.busy:
            return None
        return " ".join(self._parts) or None

    def _collect(self) -> str:
        """Куски плюс хвост после последнего разреза — весь текст диктовки."""
        if self._worker is not None:
            self._worker.join(timeout=self.max_minutes * 60)
        parts = list(self._parts)
        tail = self.frames[self._taken:]
        if tail:
            audio = np.concatenate(tail)
            if len(audio) >= SAMPLE_RATE * TAIL_MIN_SECONDS:
                parts.append(self._recognize(audio).strip())
        return " ".join(part for part in parts if part)

    def _auto_stop(self) -> None:
        with self.lock:
            if self.recording and time.time() - self.started_at >= self.max_minutes * 60:
                print(f"⏱ Авто-стоп после {self.max_minutes} мин")
                # На экране об этом надо сказать: микрофон уже закрыт, и всё,
                # что человек говорит дальше, не записывается вовсе. Раньше
                # это было видно только в журнале, куда никто не смотрит.
                self._notify(t("notice.auto_stop", minutes=self.max_minutes), 6)
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
                self._notify(t("notice.too_short"))
                print("Слишком короткая запись — пропускаю")
                return
            # Знаки препинания ставятся один раз по собранному тексту, а не по
            # кускам: на куске модель не видит, где кончается мысль, и границы
            # предложений с заглавными поехали бы. Стоит это 18 мс.
            text = self._polish(self._collect())
            if not text:
                # «Ничего не распознано» и «микрофон не звучал» — разные беды с
                # разным лечением, а выглядели одинаково. Если устройством ввода
                # оказался не микрофон, программа честно пишет цифровую тишину и
                # молча отдаёт пустоту (у Handy это открытый дефект #1899).
                # Человеку в этом случае надо чинить не дикцию, а настройки звука.
                if not len(audio) or float(np.sqrt(np.mean(audio.astype(np.float64) ** 2))) < SILENT_RMS:
                    self.last_text = "(микрофон молчит)"
                    self._notify(t("notice.no_sound"), 8)
                    print("⚠️ В записи цифровая тишина — микрофон ничего не дал")
                    return
                self.last_text = "(речь не распознана)"
                self._notify(t("notice.no_speech"))
                print("Речь не распознана")
                return
            self.last_text = text
            print(f"→ {text}")
            self._paste(text)
        except Exception as exc:
            self.last_text = f"(ошибка: {exc})"
            self._notify(t("notice.error", error=exc))
            print(f"⚠️ Ошибка распознавания: {exc}")
        finally:
            try:
                self._save_sample(audio, self.last_text)
            except Exception as exc:  # сохранение — побочное дело, диктовку ронять не должно
                print(f"Не сохранил копию записи: {exc}")
            self.busy = False

    def _focus_target(self) -> bool:
        """Возвращает фокус окну, где был курсор на старте записи. Удалось ли — ответ.

        Windows часто отказывает в передаче фокуса: это её защита от окон, лезущих
        вперёд. Отказ означает, что Ctrl+V уйдёт не в то окно, — и раз так, слать
        его нельзя вовсе. Раньше ответ не смотрели, и текст молча улетал в никуда.
        """
        import ctypes

        if not self.target_hwnd:
            return False
        try:
            ctypes.windll.user32.SetForegroundWindow(self.target_hwnd)
            time.sleep(0.15)
            return ctypes.windll.user32.GetForegroundWindow() == self.target_hwnd
        except Exception as exc:
            print(f"Не смог вернуть фокус окну: {exc}")
            return False

    def _paste(self, text: str) -> None:
        """Вставляет текст туда, где был курсор. Не вышло — оставляет в буфере.

        Диктовка остаётся в буфере обмена до тех пор, пока человек не вставит
        её сам: только это и доказывает, что текст до него дошёл.
        """
        import keyboard
        import pyperclip

        landed = self._focus_target()

        previous = ""
        try:
            previous = pyperclip.paste() or ""
        except Exception as exc:
            print(f"Не прочитал буфер обмена: {exc}")

        # Буфер обмена в Windows берётся под замок целиком, и менеджеры буфера
        # (Win+V, Ditto, Punto Switcher) держат его открытым на время своей работы.
        # Библиотека ждёт полсекунды и сдаётся. Уронить из-за этого диктовку нельзя:
        # текст уже распознан и лежит в last_text, откуда его берёт меню.
        try:
            pyperclip.copy(text)
        except Exception as exc:
            print(f"⚠️ Буфер обмена занят другой программой: {exc}")
            self._notify(t("notice.clipboard_busy"), 20)
            return

        if not landed:
            print("Окно не вышло на передний план — текст оставлен в буфере")
            self._notify(t("notice.clipboard_has_text"), 30)
        elif clipboard_holds(text):
            keyboard.send("ctrl+v")
        else:
            # Ctrl+V сейчас вставил бы чужое. Молчать нельзя, но и слать нельзя:
            # испорченный чужим текстом документ обратно не соберёшь, а диктовка
            # никуда не делась — она в last_text и в меню.
            print("⚠️ В буфере не наш текст — Ctrl+V не отправляю")
            self._notify(t("notice.clipboard_busy"), 20)
            return

        # Прежнее содержимое возвращаем ТОЛЬКО когда человек вставил сам.
        # Раньше после нашего Ctrl+V оно возвращалось по таймеру, и это стоило
        # диктовки: `landed` значит «окно вышло вперёд», а не «текст вставился».
        # Продиктовал мимо поля ввода в VS Code — окно вперёд вышло, Ctrl+V ушёл
        # в никуда, а через секунду буфер уже держал прежнее содержимое.
        # Отличить одно от другого нельзя: у приложений на Chromium системной
        # каретки нет вовсе, и Windows на вопрос «есть ли поле ввода» отвечает
        # «нет» и для панели задач, и для редактора VS Code (замерено).
        # Поэтому не гадаем: диктовка остаётся в буфере, пока не появится
        # доказательство — собственное нажатие Ctrl+V или Shift+Insert.
        if previous:
            self._watch_for_paste(previous, text)

    def _watch_for_paste(self, previous: str, ours: str) -> None:
        """Вернуть прежний буфер, когда человек вставит текст сам.

        Возврат по таймеру тут не годится: неизвестно, сколько человек будет
        искать нужное окно. Ждём само действие — Ctrl+V или Shift+Insert.
        Вставку правой кнопкой мыши так не увидеть: тогда прежнее содержимое
        просто не вернётся, а текст останется в буфере до следующей диктовки.
        """
        import keyboard

        self._forget_paste_watch()

        def on_paste():
            # Даём вставке пройти: вернём буфер сразу — приложение успеет
            # прочитать уже подменённое содержимое и вставит не то.
            threading.Timer(0.4, lambda: self._put_back(previous, ours)).start()

        for combo in ("ctrl+v", "shift+insert"):
            try:
                self._paste_hooks.append(keyboard.add_hotkey(combo, on_paste, suppress=False))
            except Exception as exc:
                print(f"Не смог следить за {combo}: {exc}")
        # Молчим, когда всё встало: строка на каждую диктовку — шум. Говорим
        # только о недостаче, иначе «возврат не работает» опять будет без причины.
        if len(self._paste_hooks) < 2:
            print(f"Слежение за ручной вставкой встало не полностью: "
                  f"{len(self._paste_hooks)} из 2 — возврат буфера может не сработать")

    def _forget_paste_watch(self) -> None:
        """Снимает слежение за вставкой. Зовётся и при новой диктовке: старое
        обещание вернуть буфер к тому времени уже неактуально."""
        import keyboard

        for hook in self._paste_hooks:
            try:
                keyboard.remove_hotkey(hook)
            except Exception:
                pass
        self._paste_hooks = []

    def _put_back(self, previous: str, ours: str) -> None:
        """Возвращает прежний буфер, но только если в нём всё ещё наш текст.

        Человек мог скопировать своё, пока мы ждали. Затирать его копию нельзя:
        он этого не просил и не увидит, что потерял.
        """
        import pyperclip

        self._forget_paste_watch()
        try:
            сейчас = pyperclip.paste()
            if сейчас == ours:
                pyperclip.copy(previous)
                print("Прежнее содержимое буфера возвращено")
            else:
                # Молчать тут нельзя: снаружи это выглядит как «возврат не
                # работает», а на деле буфер занял кто-то другой — и его копию
                # мы намеренно не трогаем.
                print(f"Возврат отменён: в буфере уже не наша диктовка "
                      f"({len(сейчас or '')} знаков вместо {len(ours)})")
        except Exception as exc:
            print(f"Не вернул буфер обмена: {exc}")


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


def open_the_log() -> None:
    """Открывает журнал в блокноте. Человеку, у которого не работает, нужен этот файл,
    а искать его по папкам он не станет — значит, надо дать одним нажатием."""
    try:
        os.startfile(log_path())
    except OSError as exc:
        show_error(t("error.log_open", path=log_path(), error=exc))


def open_help(window=None) -> None:
    """Показывает справку своим окном: разделы слева, текст справа.

    Раньше справка писалась файлом рядом с программой и открывалась блокнотом.
    Работало, но выглядело как временный файл, и оглавления не было вовсе.
    Без окна (headless) остаётся прежний путь — там рисовать нечем.
    """
    import help_text

    if window is not None:
        import voice_dialogs

        window.request(lambda root: voice_dialogs.show_help(root, help_text.TEXT))
        return

    target = APP_DIR / "Справка.txt"
    try:
        target.write_text(help_text.TEXT, encoding="utf-8")
    except OSError:  # программа в папке без права записи — кладём во временную
        import tempfile

        target = Path(tempfile.gettempdir()) / f"{APP_NAME} — справка.txt"
        target.write_text(help_text.TEXT, encoding="utf-8")
    os.startfile(target)


def open_dictionary() -> None:
    """Открывает словарь замен блокнотом, создав его с примерами при первом заходе.

    Образец не перезаписываем — в отличие от справки, тут живёт работа человека.
    """
    try:
        target = replacements.ensure_file(APP_DIR)
    except OSError:  # программа в папке без права записи — кладём во временную
        import tempfile

        target = replacements.ensure_file(Path(tempfile.gettempdir()))
    os.startfile(target)


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
               ask_for_file=None, window=None):
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
        """Окно с данными о программе.

        Раньше это была всплывающая подсказка у часов, но размер ей задаёт
        Windows: длинный текст она резала молча, и человек видел обрывок.
        Без окна (headless) остаётся короткая строка в капсуле.
        """
        if window is None:
            recorder.announce(t("about.short", app=APP_NAME, model=recorder.model_name,
                                key=hotkey.key.upper()), 6)
            return

        import voice_dialogs

        window.request(lambda root: voice_dialogs.show_about(
            root,
            app=APP_NAME,
            version=APP_VERSION,
            tagline=t("about.tagline"),
            model=t(ASR_MODELS[recorder.asr_model]) if recorder.asr_model in ASR_MODELS
            else recorder.model_name,
            hotkey=hotkey.key.upper(),
            punctuation=recorder.punctuate,
            language=messages.LANGUAGES.get(messages.language(), messages.language()),
            author=APP_AUTHOR,
            url=APP_URL,
        ))

    def choose_model(name: str):
        return lambda icon, _item=None: in_background(lambda: recorder.switch_model(name))

    model_items = [
        pystray.MenuItem(
            (lambda n, l: lambda _item: model_label(n, l))(name, label),
            choose_model(name),
            checked=(lambda n: lambda _item: recorder.asr_model == n)(name),
            radio=True,
        )
        for name, label in ASR_MODELS.items()
    ]

    def choose_language(code: str):
        return lambda icon, _item=None: in_background(
            lambda: recorder.set_interface_language(code)
        )

    # Подписи языков не переводятся: «Қазақша» человек узнаёт именно в таком
    # виде, а «Казахский» на казахском ему ничего не скажет.
    def choose_minutes(minutes: int):
        return lambda icon, _item=None: in_background(
            lambda: recorder.set_max_minutes(minutes)
        )

    length_items = [
        pystray.MenuItem(
            (lambda m: lambda _item: t("menu.minutes", minutes=m))(minutes),
            choose_minutes(minutes),
            checked=(lambda m: lambda _item: recorder.max_minutes == m)(minutes),
            radio=True,
        )
        for minutes in RECORD_MINUTES
    ]

    language_items = [
        pystray.MenuItem(
            (lambda l: lambda _item: l)(label),
            choose_language(code),
            checked=(lambda c: lambda _item: messages.language() == c)(code),
            radio=True,
        )
        for code, label in messages.LANGUAGES.items()
    ]

    icon = pystray.Icon(
        APP_NAME.lower(),
        tray_image(tray_colors()["idle"]),
        APP_TAGLINE,
        menu=pystray.Menu(
            pystray.MenuItem(lambda _item: t("menu.record"), lambda *_: recorder.toggle(),
                             default=True),
            pystray.MenuItem(lambda _item: t("menu.transcribe"), lambda *_: ask_for_file(),
                             visible=ask_for_file is not None),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda _item: t("menu.model"), pystray.Menu(*model_items)),
            pystray.MenuItem(
                lambda _item: t("menu.punctuate"),
                lambda *_: in_background(
                    lambda: recorder.set_punctuate(not recorder.punctuate)
                ),
                checked=lambda _item: recorder.punctuate,
                visible=lambda _item: recorder.asr_model not in PUNCTUATED_BY_MODEL,
            ),
            pystray.MenuItem(lambda _item: t("menu.copy_last"),
                             lambda *_: in_background(recorder.copy_last_text)),
            pystray.MenuItem(
                lambda _item: t("menu.save_samples"),
                lambda *_: in_background(
                    lambda: recorder.set_save_samples(not recorder.save_samples)
                ),
                checked=lambda _item: recorder.save_samples,
            ),
            pystray.MenuItem(
                lambda _item: t("menu.hotkey", key=hotkey.key.upper()),
                lambda *_: in_background(lambda: capture_hotkey(recorder, hotkey)),
            ),
            pystray.MenuItem(lambda _item: t("menu.record_length"),
                             pystray.Menu(*length_items)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda _item: t("menu.interface_language"),
                             pystray.Menu(*language_items)),
            pystray.MenuItem(lambda _item: t("menu.dictionary"), lambda *_: open_dictionary()),
            pystray.MenuItem(lambda _item: t("menu.help"), lambda *_: open_help(window)),
            pystray.MenuItem(lambda _item: t("menu.about"), on_about),
            pystray.MenuItem(lambda _item: t("menu.log"), lambda *_: open_the_log()),
            pystray.MenuItem(lambda _item: t("menu.quit"), on_quit),
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

    # Первым делом в журнал — обстановка. Без неё чужой журнал не разобрать:
    # не видно ни версии, ни системы, ни того, лежит ли модель на месте.
    print("=" * 60)
    print(describe_environment())
    print("=" * 60)

    trust_windows_certificates()

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
    # По умолчанию включено: без знаков текст на казахском читается как каша,
    # а русской модели этот шаг всё равно не делается.
    punctuate_on = settings.as_bool(os.getenv("VOICE_PUNCTUATE"), default=True)
    # Язык надписей на экране. Незнакомый код messages молча пропустит и оставит
    # русский: испорченная настройка не должна превращать меню в пустые строки.
    messages.set_language(os.getenv("VOICE_LANG", messages.DEFAULT))
    max_minutes = read_minutes(os.getenv("VOICE_MAX_MINUTES"))

    if args.check:
        sys.exit(check(asr_model))

    import keyboard

    # Если программа уже работает, этот вызов не вернётся: файлы уйдут ей, а мы выйдем
    lock = ensure_single_instance(args.files)
    recorder = Recorder(gain, asr_model, save_samples, max_minutes)
    recorder.punctuate = punctuate_on
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
    start_tray(recorder, window.should_quit, hotkey, ask_for_file=window.ask_for_file,
               window=window)
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
    try:
        main()
    except SystemExit:
        raise  # обычный выход, а не поломка
    except BaseException:  # noqa: BLE001 — сюда падает всё, что не поймали выше
        report_crash(*sys.exc_info())
        sys.exit(1)
