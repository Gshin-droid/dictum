"""Сборка Dictum в один exe.

    .venv\\Scripts\\python.exe build_exe.py              выпуск: exe и переносная копия
    .venv\\Scripts\\python.exe build_exe.py --only-exe   отладка: быстро, без архива

На выходе — dist/dictum.exe, один файл на 62 МБ. Веса модели внутрь НЕ
вшиваются намеренно: они весят 216 МБ, а однофайловая сборка распаковывает всё
своё содержимое во временную папку при каждом запуске — старт растянулся бы до
десятка секунд, и так каждый раз. Модель скачивается сама при первом запуске в
папку models рядом с exe.
"""

import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "build"
OUT = ROOT / "dist"
NAME = "dictum"
WINDOWS = Path(os.environ.get("SystemRoot", r"C:\Windows"))
# Откуда файлам попадать в exe можно: наш проект с его окружением, сам Python и
# система. Всё прочее — посторонний пакет, случайно оказавшийся в PATH.
ALLOWED_SOURCES = (ROOT, Path(sys.executable).resolve().parent.parent,
                   Path(sys.base_prefix), WINDOWS)
DEFAULT_MODEL = "gigaam-v3-e2e-rnnt"  # её и кладём в переносную копию
VAD_MODEL = "silero-vad"  # нарезчик длинных записей; без него расшифровка полезет в сеть
# Бухгалтерия качалки: остаётся в папке весов после скачивания и работе не нужна.
# Чужому человеку в архиве не место — он открывает его и видит непонятный сор.
LEFTOVERS = shutil.ignore_patterns(".cache")

# PyInstaller сам их не находит, а без них exe падает на первом же обращении
COLLECT_DATA = ["onnx_asr"]  # 30 служебных моделей предобработки звука
COLLECT_ALL = ["sounddevice", "soundfile"]  # PortAudio пишет, libsndfile читает файлы
COLLECT_BINARIES = ["onnxruntime"]
# Паспорт пакета (.dist-info): onnx_asr при старте спрашивает свой номер версии
# через importlib.metadata, а PyInstaller метаданные без спроса не кладёт.
COPY_METADATA = ["onnx-asr", "onnxruntime", "numpy", "soundfile"]

# Всё это могло бы приехать транзитом через чужие зависимости и утроить размер
EXCLUDE = ["torch", "faster_whisper", "ctranslate2", "av", "tkinter.test", "pytest", "IPython"]


def make_icon(path: Path) -> None:
    """Иконка exe рисуется тем же кодом, что и значок в лотке — один источник правды."""
    sys.path.insert(0, str(ROOT))
    from voice_input import TRAY_ON_LIGHT, tray_image

    image = tray_image(TRAY_ON_LIGHT["idle"])
    image.save(path, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (256, 256)])
    print(f"иконка: {path.name}")


def make_version_file(path: Path) -> None:
    """Паспорт exe: имя, версия, автор, лицензия — то, что проводник Windows
    показывает в свойствах файла на вкладке «Подробно».

    Без него exe выглядит безымянным куском кода: и человеку непонятно, что за
    файл у него на диске, и антивирусным эвристикам такой файл подозрительнее.
    Версия берётся из voice_input.py, чтобы не разъезжаться с той, что в меню.
    """
    sys.path.insert(0, str(ROOT))
    from voice_input import APP_AUTHOR, APP_NAME, APP_VERSION

    numbers = tuple(int(part) for part in APP_VERSION.split(".")) + (0,) * 4
    path.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={numbers[:4]},
    prodvers={numbers[:4]},
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', '{APP_AUTHOR}'),
      StringStruct('FileDescription', '{APP_NAME} — голосовая диктовка'),
      StringStruct('FileVersion', '{APP_VERSION}'),
      StringStruct('InternalName', '{NAME}'),
      StringStruct('LegalCopyright', '© 2026 {APP_AUTHOR}. Лицензия MIT'),
      StringStruct('OriginalFilename', '{NAME}.exe'),
      StringStruct('ProductName', '{APP_NAME}'),
      StringStruct('ProductVersion', '{APP_VERSION}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8")
    print(f"паспорт файла: {APP_NAME} {APP_VERSION}")


def stop_running() -> None:
    """Работающий exe держит сам себя — без остановки пересборка падает на «отказано в доступе»."""
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"Get-Process {NAME} -ErrorAction SilentlyContinue | Stop-Process -Force"],
        check=False, capture_output=True,
    )


def clean_path() -> str:
    """PATH для сборки: только система и наше окружение.

    Зависимые библиотеки PyInstaller ищет там же, где их искала бы Windows, —
    то есть и в PATH. У постороннего пакета папка может стоять в PATH раньше
    System32, и тогда его файлы уезжают в наш exe. Так в выпуск 1.1.2 попали
    47 библиотек рантайма Microsoft из Eclipse Adoptium JDK 17: подлинные и
    подписанные, но сборка начинала зависеть от того, что ещё стоит на машине.
    Снеси JDK — и exe соберётся из других файлов.

    Список разрешённого, а не запрещённого: перечислять чужие пакеты пришлось
    бы вечно, а сборке нужны всего три места.
    """
    keep = [WINDOWS / "System32", WINDOWS, WINDOWS / "System32" / "Wbem",
            Path(sys.executable).resolve().parent]
    return os.pathsep.join(str(path) for path in keep)


def check_origins(toc: Path) -> None:
    """Отказ, если в exe уехал файл из постороннего пакета.

    Сторож на случай, если clean_path однажды перестанет работать — например,
    PyInstaller сменит порядок поиска. Без него поломка была бы тихой: exe
    собрался, ошибки нет, а внутри чужие файлы. Список того, что вошло в
    сборку, PyInstaller оставляет сам — здесь он и проверяется.
    """
    # В файле длинный кортеж со служебными полями; нужен единственный список
    # записей вида (имя, откуда взят, тип). Ищем по форме, а не по номеру:
    # состав кортежа от версии PyInstaller к версии меняется.
    parts = ast.literal_eval(toc.read_text(encoding="utf-8"))
    entries = next(part for part in parts
                   if isinstance(part, list) and part and isinstance(part[0], tuple))
    roots = [os.path.normcase(str(root)) + os.sep for root in ALLOWED_SOURCES]
    strangers = sorted({
        source for _, source, _ in entries
        if source and not any(os.path.normcase(source).startswith(root) for root in roots)
    })
    if strangers:
        raise SystemExit(
            "В сборку попали файлы из посторонних мест:\n  "
            + "\n  ".join(strangers[:10])
            + (f"\n  ...и ещё {len(strangers) - 10}" if len(strangers) > 10 else "")
            + "\nОни нашлись через PATH. Проверить clean_path()."
        )
    print(f"происхождение: все {len(entries)} файлов из своего окружения и системы")


def build(icon: Path, version_file: Path) -> None:
    args = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--onefile",      # один файл: библиотеки внутри, модель снаружи
        "--windowed",     # без чёрного окна консоли; вывод уходит в logs/dictum.log
        "--noupx",        # сжатие UPX — частый повод для ложной тревоги антивируса
        "--name", NAME,
        "--icon", str(icon),
        "--version-file", str(version_file),
        "--distpath", str(OUT), "--workpath", str(BUILD), "--specpath", str(BUILD),
        "--paths", str(ROOT),
        # оба импортируются внутри функций, статический анализатор их не находит
        "--hidden-import", "voice_window",
        "--hidden-import", "voice_settings",
        "--hidden-import", "transcribe",
    ]
    for name in COLLECT_DATA:
        args += ["--collect-data", name]
    for name in COLLECT_ALL:
        args += ["--collect-all", name]
    for name in COLLECT_BINARIES:
        args += ["--collect-binaries", name]
    for name in COPY_METADATA:
        args += ["--copy-metadata", name]
    for name in EXCLUDE:
        args += ["--exclude-module", name]
    args.append(str(ROOT / "voice_input.py"))

    print("собираю exe, это займёт минуту-две...")
    subprocess.run(args, check=True, env={**os.environ, "PATH": clean_path()})
    check_origins(BUILD / NAME / "PKG-00.toc")


READ_ME_FIRST = """Dictum — голосовая диктовка

Нажал клавишу — говоришь — текст появляется там, где стоял курсор.

КАК ЗАПУСТИТЬ
  Двойной клик по dictum.exe. Устанавливать ничего не нужно.
  Если Windows покажет синее окно «Система Windows защитила ваш компьютер»,
  нажать ссылку «Подробнее», под ней появится кнопка «Выполнить в любом случае».

  Окна не появится: программа живёт значком рядом с часами. Он может прятаться
  под стрелочкой «Отображать скрытые значки».

КАК ПОЛЬЗОВАТЬСЯ
  F8            начать запись (внизу экрана появится полоска с волной)
  F8 ещё раз    распознать и вставить текст туда, где стоял курсор
  Esc           выбросить запись, ничего не распознавая
  правый клик по значку   настройки: модель, горячая клавиша, о программе
  правый клик по значку → Выход   закрыть программу

  Курсор нужно поставить в поле для ввода ДО нажатия F8: текст вставляется
  в то окно, которое было активным в момент начала записи.

РАСШИФРОВАТЬ ГОТОВУЮ ЗАПИСЬ
  Правый клик по значку у часов -> «Расшифровать аудиофайл…», выбрать запись.
  Рядом с ней появится текст тем же именем, с расширением .txt, и сразу
  откроется.

  Можно и перетащить файл мышкой — но НА САМ dictum.exe или на его ярлык.
  На значок у часов файлы перетащить нельзя: область уведомлений Windows их
  не принимает ни у одной программы.

  Читаются wav, mp3, ogg, opus, flac. Записи с iPhone (.m4a) сначала перевести
  в mp3 любым конвертером.
  Час записи разбирается примерно за восемь минут; пока идёт разбор, диктовка
  по клавише не работает.

ЕСЛИ КЛАВИША НЕ СРАБАТЫВАЕТ В КАКОМ-ТО ОКНЕ
  В окна, запущенные от имени администратора, обычная программа печатать не
  может. Это защита Windows, а не поломка программы: Диспетчер задач, редактор
  реестра, командная строка администратора, установщики.
  Лечится запуском самой диктовки от администратора: правый клик по dictum.exe
  -> «Запуск от имени администратора». Тогда она печатает и туда, и в обычные
  окна — ограничение работает только снизу вверх.

ЕСЛИ ПРОГРАММА НЕ ЗАПУСКАЕТСЯ
  При поломке на старте она сама показывает окно с ошибкой и называет файл
  журнала. Если окна не было — журнал лежит в папке logs рядом с dictum.exe.
  Из работающей программы его открывает пункт меню «Показать журнал».
  Этот файл и нужно прислать автору: по нему видно причину.

ВАЖНО
  Папку не распаковывать в Program Files — программе нужно право записи
  рядом с собой. Годится рабочий стол, «Документы», флешка.
  Папку models не удалять и не переименовывать: в ней распознавание речи.

  Интернет не нужен вообще: модель уже внутри, речь никуда не отправляется.

Автор: Gshin-droid. Открытый код, лицензия MIT:
https://github.com/Gshin-droid/dictum
"""


def portable() -> Path:
    """Собирает папку «распаковал и работай»: exe плюс уже скачанная модель.

    Второй способ раздачи. Один exe меньше, но у него первый запуск качает
    216 МБ — а бывает, что интернета на той машине нет вовсе или он платный.
    Здесь качать нечего: копируется всё готовое.
    """
    folder = OUT / f"{NAME}-portable"
    shutil.rmtree(folder, ignore_errors=True)
    (folder / "models").mkdir(parents=True)

    shutil.copy2(OUT / f"{NAME}.exe", folder / f"{NAME}.exe")
    (folder / "Прочти меня.txt").write_text(READ_ME_FIRST, encoding="utf-8")

    weights = OUT / "models" / DEFAULT_MODEL
    if not weights.exists():
        raise SystemExit(
            f"Нет весов в {weights}. Запустить dist/{NAME}.exe один раз — он их скачает."
        )
    print(f"копирую веса {DEFAULT_MODEL} (216 МБ, полминуты)...")
    shutil.copytree(weights, folder / "models" / DEFAULT_MODEL, ignore=LEFTOVERS)

    vad = OUT / "models" / VAD_MODEL
    if not vad.exists():
        raise SystemExit(
            f"Нет нарезчика в {vad}. Расшифруй любой файл готовым exe — он его скачает.\n"
            "Без него переносная копия полезет в интернет на первой же расшифровке."
        )
    shutil.copytree(vad, folder / "models" / VAD_MODEL, ignore=LEFTOVERS)

    print("жму в архив, это пара минут...")
    archive = shutil.make_archive(str(OUT / f"{NAME}-portable"), "zip", OUT, folder.name)
    return Path(archive)


def drop_portable() -> None:
    """Стирает переносную копию, оставшуюся от прежней сборки.

    Копия — это тот же dictum.exe, только с моделью рядом. После пересборки exe
    она превращается в копию уже не того файла, и отличить её от свежей можно
    лишь по дате: внутри архива всё выглядит одинаково. Так в выпуск 1.1.2 чуть
    не уехал архив от 1.1.1 — расхождение заметили случайно.

    Поэтому старая копия не остаётся лежать вовсе: расходиться нечему.
    """
    shutil.rmtree(OUT / f"{NAME}-portable", ignore_errors=True)
    (OUT / f"{NAME}-portable.zip").unlink(missing_ok=True)


if __name__ == "__main__":
    # Переносная копия собирается по умолчанию, а не по флагу. Флаг забывают —
    # и забытый флаг оставлял в dist архив от прежней сборки. Теперь забывчивость
    # даёт полный комплект, а урезанная сборка требует сказать это вслух.
    only_exe = "--only-exe" in sys.argv

    icon_path = BUILD / f"{NAME}.ico"
    version_path = BUILD / "version_info.txt"
    icon_path.parent.mkdir(parents=True, exist_ok=True)
    stop_running()
    make_icon(icon_path)
    make_version_file(version_path)
    build(icon_path, version_path)
    drop_portable()  # что бы ни лежало в dist, оно теперь от прежнего exe
    exe = OUT / f"{NAME}.exe"
    print(f"\nготово: {exe}  ({exe.stat().st_size / 1e6:.0f} МБ)")
    print("модель качается сама при первом запуске, рядом с exe появится папка models")

    if only_exe:
        print("\nпереносной копии нет: сборка отладочная (--only-exe).")
        print("для выпуска запустить без флага — соберётся и архив")
    else:
        archive = portable()
        print(f"\nпереносная копия: {archive}  ({archive.stat().st_size / 1e6:.0f} МБ)")
        print("распаковал, кликнул dictum.exe — работает, качать нечего")
