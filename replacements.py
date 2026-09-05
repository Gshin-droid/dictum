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
# Ниже — готовый набор. Он уже работает. Ненужное удаляйте или ставьте
# решётку в начале строки. Своё дописывайте в конец, порядок строк не важен.
# Размер списка на скорость не влияет: хоть сто правил, хоть пять тысяч.
#
# Важно про падежи. Слово сравнивается целиком, поэтому «фигма» заменится,
# а «фигме» и «фигму» — нет. Если диктуете слово в разных формах, добавьте
# строку на каждую:
#     фигма = Figma
#     фигме = Figma
#     фигму = Figma
# Так сделано не из лени: догадаться, что «телеграме» — это «телеграм», можно
# только зная русскую грамматику, а сюда пишут и казахские слова, и фамилии,
# и выдуманные названия, где никакой грамматики нет.

# --- добавлено 05.09.2026 по живым диктовкам ---
# Не выдумано, а собрано из тринадцати настоящих записей: слева ровно то, что
# программа услышала на самом деле. «Гитхаб» в списке был с самого начала, а
# модель упорно слышит «гидхаб» через «д» — правило не срабатывало ни разу.
гидхаб = GitHub
гидхаба = GitHub
гидхабе = GitHub
гидхабу = GitHub
гидхабом = GitHub
гитхаба = GitHub
гитхабе = GitHub
гитхабу = GitHub
гитхабом = GitHub
вирус тотал = VirusTotal
вирустотал = VirusTotal
микрософт = Microsoft
микрософта = Microsoft
микрософту = Microsoft
гит экшн = GitHub Actions
гит экшен = GitHub Actions
сиай = CI
си ай = CI
контрол в = Ctrl+V
контрол ц = Ctrl+C
стейбл = stable

# --- сервисы и соцсети ---
гугл = Google
ютуб = YouTube
гитхаб = GitHub
гитлаб = GitLab
телеграм = Telegram
ватсап = WhatsApp
вотсап = WhatsApp
инстаграм = Instagram
фейсбук = Facebook
твиттер = Twitter
линкедин = LinkedIn
тикток = TikTok
скайп = Skype
слак = Slack
дискорд = Discord
вайбер = Viber
нетфликс = Netflix
спотифай = Spotify
амазон = Amazon
алиэкспресс = AliExpress
вордпресс = WordPress

# --- компании ---
эпл = Apple
эппл = Apple
майкрософт = Microsoft
самсунг = Samsung
хуавей = Huawei
сяоми = Xiaomi
нвидиа = NVIDIA
интел = Intel
опенэйай = OpenAI
антропик = Anthropic

# --- программы и системы ---
эксель = Excel
эксел = Excel
ворд = Word
пауэрпоинт = PowerPoint
аутлук = Outlook
виндовс = Windows
линукс = Linux
убунту = Ubuntu
макос = macOS
андроид = Android
айфон = iPhone
айпад = iPad
макбук = MacBook
фотошоп = Photoshop
фигма = Figma
ноушен = Notion
обсидиан = Obsidian
джира = Jira
трелло = Trello
нотпад = Notepad

# --- разработка ---
джаваскрипт = JavaScript
тайпскрипт = TypeScript
реакт = React
докер = Docker
кубернетес = Kubernetes
постгрес = PostgreSQL
джейсон = JSON
хтмл = HTML
эйчтиэмэль = HTML
цсс = CSS
юрл = URL
апи = API
эйпиай = API
эскюэль = SQL
пул реквест = pull request
код ревью = code review

# --- нейросети ---
чат джипити = ChatGPT
чатджипити = ChatGPT
джипити = GPT
клод = Claude
клод код = Claude Code
джемини = Gemini
опенроутер = OpenRouter
хаггинг фейс = Hugging Face
оламма = Ollama
олама = Ollama
виспер = Whisper
гигаам = GigaAM
онникс = ONNX

# --- железо и связь ---
вайфай = Wi-Fi
вай фай = Wi-Fi
блютус = Bluetooth
блютуз = Bluetooth
юэсби = USB
хдми = HDMI
пдф = PDF
джипег = JPEG
пнг = PNG

# --- деловые сокращения ---
айти = IT
кипиай = KPI
црм = CRM
ерп = ERP
бэ ту би = B2B

# --- сервисы Казахстана ---
каспи = Kaspi
каспий банк = Kaspi Bank
халык банк = Halyk Bank
егов = eGov

# --- составные названия ---
# Слева можно писать несколько слов — заменится всё сочетание. Такое правило
# главнее одиночного: «гугл документы» сработает раньше, чем «гугл».
гугл диск = Google Диск
гугл документы = Google Документы
гугл таблицы = Google Таблицы
гугл переводчик = Google Переводчик
эпл пэй = Apple Pay
визуал студио = Visual Studio
вижуал студио код = VS Code

# --- выключены нарочно: совпадают с обычными словами ---
# Включайте, если такие слова у вас всегда означают название, а не предмет.
# зум = Zoom              — «зум» бывает и оптическим
# питон = Python          — «питон» бывает и змеёй
# редис = Redis           — «редис» бывает и овощем
# трансформер = Transformer  — «трансформер» бывает и игрушкой
# смс = SMS               — по-русски обычно пишут кириллицей
# халык = Halyk           — по-казахски «халық» значит «народ»
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


ONE_WORD = re.compile(r"\w+")


def compile_rules(rules: list[tuple[str, str]]):
    """Правила → готовая замена. Пустой список — None, замен не будет.

    Один проход по тексту, потому что замены не должны цепляться друг за друга:
    если применять правила по очереди, вывод одного попадёт под другое, и пара
    «а = б» с «б = в» превратит «а» в «в», чего человек не просил.

    Одиночные слова лежат в таблице, а не в выражении. Раньше все правила
    собирались в одно выражение из N веток, и в каждой точке текста примерялись
    все ветки подряд — время росло быстрее самого словаря: сто правил давали
    0,3 мс на фразу, тысяча уже 12,7 мс, а расшифровка часовой лекции на тысяче
    правил занимала две секунды вместо сорока миллисекунд. С таблицей время
    не зависит от размера словаря вовсе.

    Перебором остаются только многословные правила («гугл документы») и те,
    где слева есть знаки помимо букв («с++»): в таблицу по одному слову они
    не ложатся. Их единицы, а не тысячи. Обе части живут в ОДНОМ выражении —
    разделить их на два прохода нельзя, вернулась бы та самая цепочка.
    """
    if not rules:
        return None

    table: dict[str, str] = {}
    phrases: list[tuple[str, str]] = []
    for left, right in rules:
        if ONE_WORD.fullmatch(left):
            # правила уже отсортированы, длинное раньше короткого: setdefault
            # оставляет то, которое победило бы и в прежнем переборе
            table.setdefault(left.lower(), right)
        else:
            phrases.append((left, right))

    parts = []
    for number, (left, _) in enumerate(phrases):
        # Пробелы между словами — любые: человек мог поставить два подряд.
        body = r"\s+".join(re.escape(word) for word in left.split())
        parts.append(f"(?P<p{number}>{body})")
    parts.append(r"(?P<w>\w+)")  # любое слово: за ответом идём в таблицу
    pattern = re.compile(rf"(?<!\w)(?:{'|'.join(parts)})(?!\w)", re.IGNORECASE)

    def swap(match: re.Match) -> str:
        found = match.group(0)
        if match.lastgroup == "w":
            replacement = table.get(found.lower())
            # None значит «такого правила нет»; пустая строка — законная замена,
            # ей человек стирает слово-паразит, поэтому сравнение именно с None
            return found if replacement is None else _keep_case(found, replacement)
        return _keep_case(found, phrases[int(match.lastgroup[1:])][1])

    def replace(text: str) -> str:
        return pattern.sub(swap, text)

    return replace


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
        self._replace = None

    def _refresh(self) -> None:
        try:
            stamp = self.path.stat().st_mtime_ns
        except OSError:  # файла нет — замен нет, это обычное состояние
            self._replace, self._stamp = None, None
            return
        if stamp == self._stamp:
            return
        self._stamp = stamp
        try:
            text = self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            self._replace = None
            return
        self._replace = compile_rules(parse(text))

    def apply(self, text: str) -> str:
        """Текст с заменами. Пустой словарь или сломанный файл — текст как был."""
        self._refresh()
        if not self._replace or not text:
            return text
        return self._replace(text)


def ensure_file(folder: Path) -> Path:
    """Путь к словарю. Файла нет — кладёт образец с примерами."""
    path = folder / FILE_NAME
    if not path.exists():
        path.write_text(SAMPLE, encoding="utf-8")
    return path
