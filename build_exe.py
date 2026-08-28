"""Сборка голосовой диктовки в один exe.

    .venv\\Scripts\\python.exe build_exe.py

На выходе — dist/voice-dictation.exe, один файл на 67 МБ. Веса модели внутрь НЕ
вшиваются намеренно: они весят 216 МБ, а однофайловая сборка распаковывает всё
своё содержимое во временную папку при каждом запуске — старт растянулся бы до
десятка секунд, и так каждый раз. Модель скачивается сама при первом запуске в
папку models рядом с exe.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "build"
OUT = ROOT / "dist"
NAME = "voice-dictation"

# PyInstaller сам их не находит, а без них exe падает на первом же обращении
COLLECT_DATA = ["onnx_asr"]  # 30 служебных моделей предобработки звука
COLLECT_ALL = ["sounddevice"]  # библиотека записи PortAudio
COLLECT_BINARIES = ["onnxruntime"]
# Паспорт пакета (.dist-info): onnx_asr при старте спрашивает свой номер версии
# через importlib.metadata, а PyInstaller метаданные без спроса не кладёт.
COPY_METADATA = ["onnx-asr", "onnxruntime", "numpy"]

# Всё это могло бы приехать транзитом через чужие зависимости и утроить размер
EXCLUDE = ["torch", "faster_whisper", "ctranslate2", "av", "tkinter.test", "pytest", "IPython"]


def make_icon(path: Path) -> None:
    """Иконка exe рисуется тем же кодом, что и значок в лотке — один источник правды."""
    sys.path.insert(0, str(ROOT))
    from voice_input import TRAY_ON_LIGHT, tray_image

    image = tray_image(TRAY_ON_LIGHT["idle"])
    image.save(path, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (256, 256)])
    print(f"иконка: {path.name}")


def stop_running() -> None:
    """Работающий exe держит сам себя — без остановки пересборка падает на «отказано в доступе»."""
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"Get-Process {NAME} -ErrorAction SilentlyContinue | Stop-Process -Force"],
        check=False, capture_output=True,
    )


def build(icon: Path) -> None:
    args = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--onefile",      # один файл: библиотеки внутри, модель снаружи
        "--windowed",     # без чёрного окна консоли; вывод уходит в logs/voice-input.log
        "--noupx",        # сжатие UPX — частый повод для ложной тревоги антивируса
        "--name", NAME,
        "--icon", str(icon),
        "--distpath", str(OUT), "--workpath", str(BUILD), "--specpath", str(BUILD),
        "--paths", str(ROOT),
        # оба импортируются внутри функций, статический анализатор их не находит
        "--hidden-import", "voice_window",
        "--hidden-import", "voice_settings",
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
    subprocess.run(args, check=True)


if __name__ == "__main__":
    icon_path = BUILD / f"{NAME}.ico"
    icon_path.parent.mkdir(parents=True, exist_ok=True)
    stop_running()
    make_icon(icon_path)
    build(icon_path)
    exe = OUT / f"{NAME}.exe"
    print(f"\nготово: {exe}  ({exe.stat().st_size / 1e6:.0f} МБ)")
    print("модель качается сама при первом запуске, рядом с exe появится папка models")
