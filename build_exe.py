"""Сборка Dictum в один exe.

    .venv\\Scripts\\python.exe build_exe.py                 выпуск: exe и переносная копия
    .venv\\Scripts\\python.exe build_exe.py --only-exe      отладка: быстро, без архива
    .venv\\Scripts\\python.exe build_exe.py --portable-kk   копия с казахским, из готового exe
    .venv\\Scripts\\python.exe build_exe.py --module        только веса казахского модуля

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

from help_text import TEXT as READ_ME_FIRST

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
MULTILINGUAL_MODEL = "gigaam-multilingual-ctc"  # казахский, киргизский, узбекский
PUNCT_MODEL = "punct-multilang"  # знаки препинания для многоязычной модели
MODULE_NAME = "dictum-modul-kazahskiy"
KAZAKH_SUFFIX = "-kazahskiy"  # приписка к имени казахской переносной копии
# Настройки, с которыми казахская копия стартует у получателя. Многоязычная
# модель выбрана заранее: копия собрана под проверку казахского, и заставлять
# человека сначала искать пункт в меню незачем.
KAZAKH_ENV = """# Настройки Dictum. Меняются в меню значка у часов, править руками не нужно.

# Модель распознавания. Многоязычная выбрана заранее: эта копия собрана
# для казахского. Переключается в меню, пункт «Язык и модель».
ASR_MODEL=gigaam-multilingual-ctc

# Знаки препинания для многоязычной модели: 1 — ставить, 0 — не ставить.
VOICE_PUNCTUATE=1

# Горячая клавиша записи.
VOICE_HOTKEY=f8
"""
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


def stop_running() -> bool:
    """Гасит работающую программу и говорит, была ли она запущена.

    Гасить приходится: работающий exe держит сам себя, и пересборка падает на
    «отказано в доступе». А вот ответ нужен, чтобы в конце поднять её обратно —
    см. start_again.
    """
    answer = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"$found = @(Get-Process {NAME} -ErrorAction SilentlyContinue);"
         " $found | Stop-Process -Force; $found.Count"],
        check=False, capture_output=True, text=True,
    )
    return answer.stdout.strip() not in ("", "0")


def start_again() -> None:
    """Поднимает программу обратно, если сборка её погасила.

    Без этого каждая сборка молча оставляла машину без диктовки: программа живёт
    значком у часов, окна у неё нет, и заметить пропажу можно только по тому,
    что перестала работать горячая клавиша. Так и вышло дважды подряд.

    Сборка обязана вернуть машину в то состояние, в котором её взяла.
    """
    exe = OUT / f"{NAME}.exe"
    if not exe.is_file():  # сборка не дошла до exe — поднимать нечего
        print("поднять программу обратно не вышло: exe нет")
        return
    subprocess.Popen([str(exe)], cwd=str(OUT), close_fds=True)
    print(f"программа поднята обратно: {exe}")


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
    # Пути приводим к настоящему месту на диске, а не сличаем строками.
    # C:\my_projects — соединение (junction) на F:\my_projects: папка одна, а
    # написана по-разному, и сторож объявлял чужими собственные файлы сборки.
    def настоящий(путь) -> str:
        return os.path.normcase(str(Path(путь).resolve()))

    roots = [настоящий(root) + os.sep for root in ALLOWED_SOURCES]
    strangers = sorted({
        source for _, source, _ in entries
        if source and not any(настоящий(source).startswith(root) for root in roots)
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
        "--hidden-import", "voice_dialogs",
        "--hidden-import", "voice_settings",
        "--hidden-import", "transcribe",
        "--hidden-import", "punctuate",
        "--hidden-import", "help_text",
        "--hidden-import", "replacements",
        "--hidden-import", "messages",
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


def portable(extra: tuple[str, ...] = (), suffix: str = "", env: str = "") -> Path:
    """Собирает папку «распаковал и работай»: exe плюс уже скачанные модели.

    Второй способ раздачи. Один exe меньше, но у него первый запуск качает
    216 МБ — а бывает, что интернета на той машине нет вовсе или он платный.
    Здесь качать нечего: копируется всё готовое.

    extra — папки весов сверх обязательных. Через них собирается казахская копия;
    руками её однажды соберут в спешке и забудут пунктуатор, а команда не забудет.
    suffix — приписка к имени, чтобы казахская копия не затирала обычную.
    env — готовые настройки рядом с программой, если копия собрана под задачу.
    """
    folder = OUT / f"{NAME}-portable{suffix}"
    shutil.rmtree(folder, ignore_errors=True)
    (folder / "models").mkdir(parents=True)

    shutil.copy2(OUT / f"{NAME}.exe", folder / f"{NAME}.exe")
    (folder / "Прочти меня.txt").write_text(READ_ME_FIRST, encoding="utf-8")
    if env:
        (folder / ".env").write_text(env, encoding="utf-8")

    for name in (DEFAULT_MODEL, VAD_MODEL, *extra):
        weights = OUT / "models" / name
        if not weights.exists():
            raise SystemExit(
                f"Нет весов {name} в {weights}.\n"
                f"Выбрать нужную модель в меню готового dist/{NAME}.exe и продиктовать "
                "одну фразу — он скачает и её, и всё, что к ней прилагается."
            )
        size = sum(f.stat().st_size for f in weights.rglob("*") if f.is_file())
        print(f"копирую веса {name} ({size / 1e6:.0f} МБ)...")
        shutil.copytree(weights, folder / "models" / name, ignore=LEFTOVERS)

    print("жму в архив, это пара минут...")
    archive = shutil.make_archive(str(OUT / f"{NAME}-portable{suffix}"), "zip", OUT, folder.name)
    return Path(archive)


def module_archive() -> Path:
    """Собирает отдельный архив казахского модуля: две папки весов.

    Нужен для машины без интернета: человек распаковывает его в models/ рядом с
    программой, и при следующем запуске пункт меню перестаёт просить закачку.
    Кладём обе папки сразу — без пунктуатора текст пойдёт сплошняком, и модуль
    будет выглядеть сломанным, хотя распознавание работает.
    """
    staging = OUT / MODULE_NAME
    shutil.rmtree(staging, ignore_errors=True)
    (staging / "models").mkdir(parents=True)

    for name in (MULTILINGUAL_MODEL, PUNCT_MODEL):
        source = OUT / "models" / name
        if not source.exists():
            raise SystemExit(
                f"Нет весов {name} в {source}.\n"
                "Выбрать «Многоязычная» в меню готового exe и продиктовать одну фразу — "
                "он скачает и модель, и знаки препинания."
            )
        shutil.copytree(source, staging / "models" / name, ignore=LEFTOVERS)

    (staging / "Как поставить.txt").write_text(
        "Казахский модуль для Dictum\n"
        f"{'-' * 40}\n\n"
        "Папку models из этого архива положить рядом с dictum.exe, согласившись\n"
        "объединить с уже существующей папкой models. Перезапустить программу.\n\n"
        "В меню значка у часов, в пункте «Язык и модель», выбрать «Многоязычная».\n"
        "Знаки препинания включатся сами; выключаются там же, отдельным пунктом.\n\n"
        "Интернет для этого не нужен.\n",
        encoding="utf-8",
    )

    print("жму модуль в архив...")
    archive = shutil.make_archive(str(OUT / MODULE_NAME), "zip", OUT, staging.name)
    return Path(archive)


def drop_portable() -> None:
    """Стирает переносную копию, оставшуюся от прежней сборки.

    Копия — это тот же dictum.exe, только с моделью рядом. После пересборки exe
    она превращается в копию уже не того файла, и отличить её от свежей можно
    лишь по дате: внутри архива всё выглядит одинаково. Так в выпуск 1.1.2 чуть
    не уехал архив от 1.1.1 — расхождение заметили случайно.

    Поэтому старая копия не остаётся лежать вовсе: расходиться нечему.
    """
    for suffix in ("", KAZAKH_SUFFIX):
        shutil.rmtree(OUT / f"{NAME}-portable{suffix}", ignore_errors=True)
        (OUT / f"{NAME}-portable{suffix}.zip").unlink(missing_ok=True)


if __name__ == "__main__":
    # Переносная копия собирается по умолчанию, а не по флагу. Флаг забывают —
    # и забытый флаг оставлял в dist архив от прежней сборки. Теперь забывчивость
    # даёт полный комплект, а урезанная сборка требует сказать это вслух.
    only_exe = "--only-exe" in sys.argv

    # Модуль собирается из уже скачанных весов и exe не трогает вовсе:
    # пересобирать программу ради упаковки чужих гигабайт незачем.
    if "--portable-kk" in sys.argv:
        kk = portable(extra=(MULTILINGUAL_MODEL, PUNCT_MODEL),
                      suffix=KAZAKH_SUFFIX, env=KAZAKH_ENV)
        print(f"\nказахская переносная копия: {kk}  ({kk.stat().st_size / 1e6:.0f} МБ)")
        print("распаковал, кликнул dictum.exe — говорит по-казахски, качать нечего")
        sys.exit(0)

    if "--module" in sys.argv:
        module_zip = module_archive()
        print(f"\nмодуль: {module_zip}  ({module_zip.stat().st_size / 1e6:.0f} МБ)")
        print("получателю: распаковать в папку с dictum.exe, слив папки models")
        sys.exit(0)

    icon_path = BUILD / f"{NAME}.ico"
    version_path = BUILD / "version_info.txt"
    icon_path.parent.mkdir(parents=True, exist_ok=True)
    # Работала — обязана работать и после. try/finally, а не строка в конце:
    # иначе упавшая сборка оставляет машину без диктовки, и это как раз тот
    # случай, когда человек узнаёт о пропаже сам, по неработающей клавише.
    was_running = stop_running()
    try:
        make_icon(icon_path)
        make_version_file(version_path)
        build(icon_path, version_path)
    finally:
        if was_running:
            start_again()

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
