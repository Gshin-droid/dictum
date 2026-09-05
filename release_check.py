"""Проверка собранного exe перед раздачей людям.

Сначала сверяет своё: не старше ли exe исходников и тот ли exe лежит в
переносном архиве. Не сошлось — отказ, до VirusTotal дело не доходит.

    .venv\\Scripts\\python.exe release_check.py dist\\dictum.exe            только посмотреть
    .venv\\Scripts\\python.exe release_check.py dist\\dictum.exe --upload   можно и загрузить

Сначала спрашивает VirusTotal об отпечатке файла: такой файл могли проверять и
до нас, тогда отчёт приходит мгновенно и грузить 60 МБ не нужно. Не знают — без
флага --upload скрипт на этом и остановится.

Загрузка нарочно спрятана за флаг: файл ложится к ним навсегда и становится
доступен их подписчикам, отменить это нельзя. Необратимое действие не должно
случаться от простого запуска.

Ключ берётся из переменной окружения VT_API_KEY. Заводится один раз:
    [Environment]::SetEnvironmentVariable('VT_API_KEY', 'ключ', 'User')

Инструмент выпуска, а не часть программы: в exe не попадает, зависимостей не
добавляет — только стандартная библиотека.
"""

import argparse
import hashlib
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile
import zlib
from pathlib import Path

API = "https://www.virustotal.com/api/v3"
DIRECT_UPLOAD_LIMIT = 32 * 1024 * 1024  # больше — только через отдельный адрес
POLL_SECONDS = 20  # бесплатный тариф: 4 запроса в минуту, чаще спрашивать нельзя
ASK_TIMEOUT = 120  # на вопрос об отпечатке хватает с запасом
# Отправка файла — другое дело: 64 МБ вверх по домашнему каналу идут четверть
# часа, и прежние 600 секунд обрывали загрузку дважды подряд на полпути.
SEND_TIMEOUT = 3600
POLL_ATTEMPTS = 30  # то есть ждём результат до десяти минут

ROOT = Path(__file__).resolve().parent
# Исходники программы. Exe, собранный раньше любого из них, — не тот, что правили.
SOURCES = ("voice_input.py", "voice_window.py", "voice_settings.py", "transcribe.py",
           "build_exe.py")


def api_key() -> str:
    """Ключ из окружения. Нет в окружении — смотрим в реестр.

    Свежесозданную переменную видят только программы, запущенные ПОСЛЕ неё:
    Windows раздаёт окружение при старте процесса и задним числом не обновляет.
    Реестр же отдаёт значение сразу, без перезапуска редактора и терминала.
    """
    key = os.getenv("VT_API_KEY")
    if key:
        return key.strip()
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as env:
            return str(winreg.QueryValueEx(env, "VT_API_KEY")[0]).strip()
    except (OSError, ImportError):  # нет такой переменной либо мы не на Windows
        sys.exit(
            "Нет ключа VT_API_KEY. Завести один раз в PowerShell:\n"
            "  [Environment]::SetEnvironmentVariable('VT_API_KEY', 'ключ', 'User')"
        )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def crc32(path: Path) -> int:
    """Контрольная сумма файла. Zip хранит такую же для каждой записи внутри —
    поэтому сверить exe с exe в архиве можно, не распаковывая архив."""
    value = 0
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            value = zlib.crc32(block, value)
    return value


def same_build(exe: Path) -> None:
    """Отказ, если раздаваемые файлы разошлись между собой или с исходником.

    Два расхождения, которые глазами не видны вовсе, а по датам файлов заметны
    только если знать, куда смотреть:

    1. exe собран до последней правки кода — раздаём не то, что написали;
    2. в переносном архиве лежит exe от прежней сборки — а качают чаще архив.

    Про версию отдельно спрашивать нечего: номер попадает в exe из APP_VERSION
    при сборке. Exe не старше исходников — значит и версия в нём оттуда же.
    Спросить сам exe нельзя: у оконной сборки --version печатает в никуда.

    Проверка стоит до вопроса VirusTotal: незачем спрашивать про файл, который
    в выпуск всё равно не пойдёт.
    """
    built = exe.stat().st_mtime
    stale = [name for name in SOURCES
             if (ROOT / name).is_file() and (ROOT / name).stat().st_mtime > built]
    if stale:
        sys.exit(
            f"{exe.name} собран раньше, чем правили {', '.join(stale)}.\n"
            "Пересобрать: .venv\\Scripts\\python.exe build_exe.py"
        )

    archive = exe.with_name(f"{exe.stem}-portable.zip")
    if not archive.is_file():
        print(f"Переносной копии рядом нет ({archive.name}) — сверять не с чем.\n")
        return

    inside = f"{exe.stem}-portable/{exe.name}"
    with zipfile.ZipFile(archive) as pack:
        try:
            packed = pack.getinfo(inside).CRC
        except KeyError:
            sys.exit(f"В {archive.name} нет {inside} — архив собран не тем скриптом.")
    if packed != crc32(exe):
        sys.exit(
            f"В {archive.name} лежит другой {exe.name} — копия от прежней сборки.\n"
            "Пересобрать: .venv\\Scripts\\python.exe build_exe.py"
        )
    print(f"Исходники не новее сборки, в архиве тот же {exe.name}.\n")


def multipart(field: str, filename: str, payload: bytes) -> tuple[bytes, str]:
    """Тело запроса на загрузку файла. Своё, потому что в стандартной библиотеке его нет."""
    boundary = uuid.uuid4().hex
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'.encode(),
        f"Content-Type: {mime}\r\n\r\n".encode(),
        payload,
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    return body, f"multipart/form-data; boundary={boundary}"


def request(url: str, key: str, data: bytes | None = None, content_type: str | None = None,
            timeout: int = ASK_TIMEOUT):
    """Запрос к VirusTotal. Возвращает разобранный ответ или None, если файла у них нет."""
    headers = {"x-apikey": key, "accept": "application/json"}
    if content_type:
        headers["content-type"] = content_type
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, data=data, headers=headers), timeout=timeout
        ) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        if exc.code == 401:
            sys.exit("VirusTotal не принял ключ: проверь VT_API_KEY")
        if exc.code == 429:
            sys.exit("Исчерпана норма запросов (4 в минуту, 500 в сутки) — подождать и повторить")
        sys.exit(f"VirusTotal ответил ошибкой {exc.code}: {exc.read()[:300].decode(errors='replace')}")


def verdict(stats: dict, results: dict, digest: str) -> str:
    """Отчёт по-русски. Главное — сколько движков ругаются и какие именно."""
    bad = {
        name: value
        for name, value in sorted(results.items())
        if value.get("category") in {"malicious", "suspicious"}
    }
    checked = sum(v for k, v in stats.items() if k != "type-unsupported")
    lines = [f"Проверено движков: {checked}, ругаются: {len(bad)}"]
    if not bad:
        lines.append("Чисто — ни один антивирус не считает файл опасным.")
    else:
        lines.append("")
        for name, value in bad.items():
            lines.append(f"  {name}: {value.get('result') or value['category']}")
        lines.append("")
        lines.append(
            "Несколько находок у малоизвестных движков — обычное дело для программ,"
            " собранных PyInstaller, тем более с перехватом клавиатуры."
            " Тревожно, если в списке Microsoft, Kaspersky, ESET, Avast или Dr.Web."
        )
    lines.append("")
    lines.append(f"Отчёт: https://www.virustotal.com/gui/file/{digest}/detection")
    return "\n".join(lines)


def upload(path: Path, key: str) -> str:
    """Отправляет файл и возвращает номер проверки. Большие файлы идут через отдельный адрес."""
    size = path.stat().st_size
    if size > DIRECT_UPLOAD_LIMIT:
        print(f"Файл {size / 1e6:.0f} МБ — прошу адрес для большой загрузки...")
        url = request(f"{API}/files/upload_url", key)["data"]
    else:
        url = f"{API}/files"
    body, content_type = multipart("file", path.name, path.read_bytes())
    print(f"Загружаю {size / 1e6:.0f} МБ. На домашнем канале это четверть часа — не пугайся тишине.")
    ответ = request(url, key, data=body, content_type=content_type, timeout=SEND_TIMEOUT)
    return ответ["data"]["id"]


def wait_for(analysis_id: str, key: str) -> tuple[dict, dict]:
    for attempt in range(POLL_ATTEMPTS):
        answer = request(f"{API}/analyses/{analysis_id}", key)["data"]["attributes"]
        if answer["status"] == "completed":
            return answer["stats"], answer["results"]
        print(f"  проверка идёт ({answer['status']}), жду {POLL_SECONDS} с...")
        time.sleep(POLL_SECONDS)
    sys.exit("VirusTotal не закончил проверку за десять минут — посмотреть на сайте по отпечатку")


def main() -> None:
    parser = argparse.ArgumentParser(description="Проверка exe на VirusTotal")
    parser.add_argument("file", type=Path, help="файл для проверки")
    parser.add_argument("--upload", action="store_true",
                        help="разрешить загрузку, если такого файла у них ещё нет")
    args = parser.parse_args()

    if not args.file.is_file():
        sys.exit(f"Нет файла {args.file}")

    same_build(args.file)
    key = api_key()
    digest = sha256(args.file)
    print(f"{args.file.name}: {args.file.stat().st_size / 1e6:.0f} МБ")
    print(f"отпечаток: {digest}\n")

    known = request(f"{API}/files/{digest}", key)
    if known:
        attrs = known["data"]["attributes"]
        print("Такой файл уже проверяли — загружать не нужно.\n")
        print(verdict(attrs["last_analysis_stats"], attrs["last_analysis_results"], digest))
        return

    if not args.upload:
        print("Этого файла у VirusTotal нет.")
        print("Загрузить его — необратимо: файл останется у них и станет доступен подписчикам.")
        print("Согласен — повторить запуск с флагом --upload.")
        return

    stats, results = wait_for(upload(args.file, key), key)
    print()
    print(verdict(stats, results, digest))


if __name__ == "__main__":
    main()
