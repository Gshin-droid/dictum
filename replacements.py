"""Словарь замен: правит слова, которые модель слышит верно, а пишет не так.

Многоязычная модель переводит английские названия в кириллицу — «гугл» вместо
«Google», «эксел» вместо «Excel». Дообучением такое не лечится: модель не
ошиблась, она так научена. А ещё у каждого человека свой набор слов, которых
модель не знает вовсе, — фамилии, названия проектов, термины. Угадать их за
него нельзя, поэтому список ведёт он сам.

Файл лежит рядом с программой, правится блокнотом, перечитывается сам после
каждой правки — перезапуск не нужен.
"""

import re
from pathlib import Path

FILE_NAME = "Замены.txt"

SAMPLE = """\
# Словарь замен Dictum.
#
# Слева — то, что услышала программа, справа — то, что должно получиться.
# Разделитель — знак «равно». Строки, начинающиеся с решётки, пропускаются.
# Регистр слева не важен: «гугл» и «Гугл» подойдут оба.
# Файл перечитывается после сохранения, перезапускать программу не нужно.
#
# Раскомментируйте нужные строки (уберите решётку) или впишите свои.

# гугл = Google
# гитхаб = GitHub
# эксел = Excel
# телеграм = Telegram

# Слева можно писать несколько слов — тогда заменится всё сочетание:
# дзен конвейер = Дзен-конвейер
"""


def parse(text: str) -> list[tuple[str, str]]:
    """Строки файла → пары «что искать, чем заменить».

    Длинные левые части идут раньше коротких: иначе правило «дзен» сработает
    первым и до правила «дзен конвейер» дело уже не дойдёт.
    """
    rules = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        left, sign, right = line.partition("=")
        left, right = left.strip(), right.strip()
        if sign and left:
            rules.append((left, right))
    rules.sort(key=lambda rule: -len(rule[0]))
    return rules


def compile_rules(rules: list[tuple[str, str]]):
    """Все правила в одно выражение — чтобы замены не цеплялись друг за друга.

    Если применять правила по очереди, вывод одного попадает под другое:
    «а = б» и «б = в» вместе превратят «а» в «в», чего человек не просил.
    Один проход по тексту такую цепочку исключает.
    """
    if not rules:
        return None, []
    parts = []
    for number, (left, _) in enumerate(rules):
        # Пробелы между словами — любые: человек мог поставить два подряд.
        body = r"\s+".join(re.escape(word) for word in left.split())
        parts.append(f"(?P<r{number}>{body})")
    return re.compile(rf"(?<!\w)(?:{'|'.join(parts)})(?!\w)", re.IGNORECASE), rules


def _keep_case(found: str, replacement: str) -> str:
    """С заглавной услышали — с заглавной и отдаём: замена стоит в начале фразы."""
    if found[:1].isupper() and replacement[:1].islower():
        return replacement[:1].upper() + replacement[1:]
    return replacement


class Dictionary:
    """Правила из файла. Файл изменился — перечитываются сами."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._stamp = None
        self._pattern = None
        self._rules: list[tuple[str, str]] = []

    def _refresh(self) -> None:
        try:
            stamp = self.path.stat().st_mtime_ns
        except OSError:  # файла нет — замен нет, это обычное состояние
            self._pattern, self._rules, self._stamp = None, [], None
            return
        if stamp == self._stamp:
            return
        self._stamp = stamp
        try:
            text = self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            self._pattern, self._rules = None, []
            return
        self._pattern, self._rules = compile_rules(parse(text))

    def apply(self, text: str) -> str:
        """Текст с заменами. Пустой словарь или сломанный файл — текст как был."""
        self._refresh()
        if not self._pattern or not text:
            return text

        def swap(match: re.Match) -> str:
            number = int(match.lastgroup[1:])
            return _keep_case(match.group(0), self._rules[number][1])

        return self._pattern.sub(swap, text)


def ensure_file(folder: Path) -> Path:
    """Путь к словарю. Файла нет — кладёт образец с примерами."""
    path = folder / FILE_NAME
    if not path.exists():
        path.write_text(SAMPLE, encoding="utf-8")
    return path
