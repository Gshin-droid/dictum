"""Знаки препинания для многоязычной модели.

Русская модель `gigaam-v3-e2e-rnnt` ставит их сама — ей этот файл не нужен.
Многоязычная выдаёт поток букв строчными: её словарь состоит из 70 символов,
где нет ни точки, ни запятой, ни дефиса. Знаки приходится доставлять отдельно.

Модель здесь разметочная, а не языковая: она не сочиняет текст, а для каждого
слова решает, какой знак стоит после него и с заглавной ли оно начинается.
Слова на выходе обязаны быть теми же — на этом и стоят обе проверки ниже.

Разбор решений: docs/kazahskiy-modul.md
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

MODEL_DIR = "punct-multilang"  # имя папки в models/
ONNX_NAME = "punct-xlmr-int8.onnx"
SPE_NAME = "punct-xlmr.model"
# Модель 1-800-BAD-CODE/xlm-roberta_punctuation_fullstop_truecase (Apache-2.0),
# у неё обрезан словарь и сжаты веса: 1061 МБ → 107 МБ. Как именно и с какими
# числами — data/punct-training/obrezka.py и docs/kazahskiy-modul.md. Готовый
# файл лежит у выпуска на GitHub: HuggingFace отдаёт только несжатый.
RELEASE = "https://github.com/Gshin-droid/dictum/releases/download/punct-xlmr-1"
# Прежние веса (punct_cap_seg_47lang, 233 МБ). Названы поимённо, чтобы удалить
# их у тех, кто уже качал: новые лежат в той же папке под другими именами, и без
# уборки на диске остаётся четверть гигабайта мёртвого груза.
OLD_FILES = ("punct_cap_seg_47lang.onnx", "spe_unigram_64k_lowercase_47lang.model")

# Порядок важен: модель отдаёт номер в этом списке. Взят из config.yaml модели,
# сюда переписан целиком, чтобы не тянуть omegaconf ради тридцати строк.
# Номер 1 — метка «сокращение» (<ACRONYM>): знака препинания за ней нет, регистр
# модель сообщает отдельным выходом, поэтому у нас она пустая. Но место занимать
# обязана: без неё все знаки съедут на единицу, и текст поедет молча.
POST_LABELS = ["", "", ".", ",", "?", "？", "，", "。", "、", "・", "।", "؟", "،",
               ";", "።", "፣", "፧"]
PRE_LABELS = ["", "¿"]  # перевёрнутый вопрос нужен испанскому, нам — нет
MAX_SUBWORDS = 256  # окно модели; казахское слово дробится в среднем на 2,6 куска
LEADING = ".,?;:!"  # с этого текст начинаться не может


def assemble(pieces: list[str], pre: list[int], post: list[int],
             cap: list[list[bool]], seg: list[bool]) -> str:
    """Собирает готовую строку из кусков слов и предсказаний модели.

    Списки идут параллельно: на каждый кусок слова свои четыре решения —
    знак перед, знак после, регистр каждой буквы, конец предложения.
    """
    sentences: list[str] = []
    chars: list[str] = []
    boundary = False  # границу слова встретили, пробел ещё не поставили
    for i, piece in enumerate(pieces):
        body = piece[1:] if piece.startswith("▁") else piece
        if piece.startswith("▁"):
            boundary = True
        if not body:
            # Голый значок пробела без единой буквы. Границу слова он означает —
            # её помним, иначе слова склеятся. А знак, предсказанный на нём,
            # цеплять не к чему: он вылезал в начало строки как «.Рахмет».
            continue
        if boundary and chars:
            chars.append(" ")
        boundary = False
        if PRE_LABELS[pre[i]]:
            chars.append(PRE_LABELS[pre[i]])
        start = 1 if piece.startswith("▁") else 0
        for j, char in enumerate(body, start=start):
            upper = cap[i][j] if j < len(cap[i]) else False
            chars.append(char.upper() if upper else char)
        if POST_LABELS[post[i]]:
            chars.append(POST_LABELS[post[i]])
        if seg[i] and chars:
            sentences.append("".join(chars).strip())
            chars = []
    if chars:
        sentences.append("".join(chars).strip())
    return " ".join(sentences)


def split_words(words: list[str], cost, limit: int = MAX_SUBWORDS) -> list[list[str]]:
    """Режет список слов на группы, влезающие в окно модели.

    Длину считаем сложением длин отдельных слов, а не перекодировкой всей
    растущей строки: разбивщик работает почти по словам, поэтому сумма чуть
    завышена — и это в нужную сторону, с запасом.

    Слово, которое само длиннее окна, кладётся в свою группу как есть: модель
    его обрежет, зато слово не пропадёт. Потеря слова означала бы, что проверка
    целости отбросит знаки на всём тексте разом.
    """
    reserve = 2  # служебные токены начала и конца
    groups: list[list[str]] = []
    current: list[str] = []
    size = 0
    for word in words:
        weight = max(1, cost(word))
        if current and size + weight + reserve > limit:
            groups.append(current)
            current, size = [word], weight
        else:
            current.append(word)
            size += weight
    if current:
        groups.append(current)
    return groups


def _words(text: str) -> list[str]:
    """Слова без знаков и регистра — для сверки «модель ничего не переписала»."""
    return re.findall(r"\w+", unicodedata.normalize("NFC", text).lower())


def guard(before: str, after: str) -> str:
    """Отдаёт обработанный текст, если он цел, иначе исходный — без знаков.

    Проверок две, и одной мало. Первая ловит переписанные и склеенные слова.
    Вторая — испорченную расстановку при целых словах: знак в начале строки
    первая проверка пропускает, потому что слова-то на месте.
    """
    if _words(before) != _words(after):
        return before
    first = after[:1]
    # Проверка на непустоту обязательна: пустая строка в Python считается
    # подстрокой чего угодно, и без неё `"" in LEADING` истинно.
    if first and first in LEADING:
        return before
    return after


# Дефиса нет в алфавите модели распознавания — там 71 знак, и его среди них нет.
# Поэтому «какие то» и «из за» приходят разорванными, и вернуть дефис можно
# только снаружи. Список поимённый, и это не лень, а замер: правило «клеить всё,
# что кончается на -то или -либо» ломает больше, чем чинит. В записях «либо»
# встретилось девять раз и все девять — самостоятельный союз («стрижка либо
# укладка»), а «то» бывает указательным словом («то, что мы создаём»).
HYPHEN_PRONOUNS = (
    "что кто кого кому кем чем чего чему чей чья чьё чьи "
    "какой какая какое какие какого какому каким какую каких "
    "где куда откуда когда как почему зачем сколько"
).split()
# Пары, где второе слово без первого не живёт: «таки», «сюда» после «туда»,
# «первых» после «во». Спорных случаев у них нет.
HYPHEN_PAIRS = [
    ("из", "за"), ("из", "под"),
    ("все", "таки"), ("всё", "таки"),
    ("чуть", "чуть"), ("давным", "давно"), ("еле", "еле"), ("туда", "сюда"),
    ("во", "первых"), ("во", "вторых"),
]
# Наречий на «по-…ому» здесь нет намеренно: «по моему мнению», «по другому
# пути», «по своему усмотрению» — законные предлог с местоимением, и они
# встречаются чаще наречия. А «по-русски» спутать не с чем: существительное
# в дательном падеже на «-ски» не оканчивается.
_HYPHEN = re.compile(
    r"(?<![\w-])(?:"
    r"(?:" + "|".join(HYPHEN_PRONOUNS) + r")\s+(?:то|нибудь|либо)"
    r"|кое\s+(?:что|кто|кого|кому|где|куда|как|какой|какая|какие|каких)"
    r"|по\s+\w+(?:ски|цки)"
    r"|" + "|".join(rf"{a}\s+{b}" for a, b in HYPHEN_PAIRS) +
    r")(?![\w-])",
    re.IGNORECASE,
)


# Точка, вопрос и восклицание закрывают предложение — следующая буква заглавная.
# Правило языковое, а не модельное, поэтому и живёт у нас, а не в весах.
_POSLE_TOCHKI = re.compile(r"([.!?]\s+)([^\W\d_])")


def capitalize_sentences(text: str) -> str:
    """Заглавная после точки. Модель ставит её сама — но не всегда.

    Живая диктовка это и вскрыла: «...заново их подключил. сейчас осуществляю».
    Модель поставила точку, но признак конца предложения не выставила и регистр
    следующего слова оставила строчным — два её выхода разошлись между собой.
    Замер этого не видел вовсе: во FLEURS каждая фраза — одно предложение, и
    вторых предложений внутри одного текста там не бывает.

    Исключений у правила нет ни в русском, ни в казахском, ни в английском:
    точка внутри слова к нам прийти не может, в алфавите распознавания её нет.
    Первая буква текста — забота модели: там она не ошибается.
    """
    return _POSLE_TOCHKI.sub(lambda m: m.group(1) + m.group(2).upper(), text)


def hyphens(text: str) -> str:
    """Возвращает дефис туда, где модель поставила пробел.

    Регистр сохраняется сам: подменяется только пробел, буквы остаются как были.
    Уже склеенное правило не трогает — соседний дефис закрыт проверками по краям.
    """
    return _HYPHEN.sub(lambda m: re.sub(r"\s+", "-", m.group(0)), text)


def missing(models_dir: Path) -> bool:
    """Весов нет и их придётся качать. Спрашивают до того, как показать надпись:
    «готовлю знаки препинания» на двухминутной закачке выглядит зависанием."""
    folder = models_dir / MODEL_DIR
    return not all((folder / name).exists() for name in (ONNX_NAME, SPE_NAME))


def ensure_weights(models_dir: Path, progress=None) -> Path:
    """Папка с весами пунктуатора. Файлов нет — качает их с выпуска на GitHub.

    `progress` зовётся долей скачанного от нуля до единицы — чтобы человек видел,
    что идёт работа, а не остановка.

    Кладём рядом с программой, а не в скрытый кеш пользователя: переносная
    копия должна работать без сети, и модуль в неё копируется целиком.
    """
    import urllib.request

    folder = models_dir / MODEL_DIR
    folder.mkdir(parents=True, exist_ok=True)
    for name in (ONNX_NAME, SPE_NAME):
        if (folder / name).exists():
            continue
        print(f"Скачиваю {name} — знаки препинания, 107 МБ на двоих, один раз")
        # Качаем во временное имя: оборванная закачка не должна выглядеть как
        # готовый файл, иначе при следующем запуске программа возьмёт огрызок.
        временный = folder / (name + ".part")
        доложить = None
        if progress is not None:
            def доложить(кусков, размер_куска, всего, _name=name):
                if всего > 0:
                    progress(min(1.0, кусков * размер_куска / всего))
        urllib.request.urlretrieve(f"{RELEASE}/{name}", временный, доложить)
        временный.replace(folder / name)
    for старый in OLD_FILES:
        if (folder / старый).exists():
            print(f"Удаляю прежние веса: {старый}")
            (folder / старый).unlink()
    return folder


class Punctuator:
    """Расставляет знаки препинания и заглавные буквы. Слова не меняет."""

    def __init__(self, folder: Path) -> None:
        import onnxruntime as ort
        import sentencepiece as spm

        # Файл читаем сами и отдаём байтами. Путь строкой отдавать нельзя:
        # sentencepiece написан на C++ и открывает файл через узкий интерфейс
        # Windows, где русские буквы в пути превращаются в вопросительные знаки.
        # Программу распаковывают в «C:\Users\Гена\Рабочий стол» — там она и
        # падала с «No such file or directory» на файле, лежащем на месте.
        # onnxruntime рядом такой путь открывает нормально, дело только в этом.
        self._sp = spm.SentencePieceProcessor(
            model_proto=(folder / SPE_NAME).read_bytes())
        self._session = ort.InferenceSession(str(folder / ONNX_NAME))
        self._input = self._session.get_inputs()[0].name

    def apply(self, text: str) -> str:
        """Текст без знаков → текст со знаками. Абзацы сохраняются.

        Порча текста невозможна по построению: результат каждой строки
        проходит обе проверки, и при любом сомнении отдаётся исходник.
        """
        if not text.strip():
            return text
        return "\n".join(hyphens(self._line(line)) for line in text.split("\n"))

    def _line(self, line: str) -> str:
        if not line.strip():
            return line
        words = line.split()
        groups = split_words(words, lambda w: len(self._sp.EncodeAsIds(w)))
        done = " ".join(self._chunk(" ".join(group)) for group in groups)
        return guard(line, capitalize_sentences(done))

    def _chunk(self, text: str) -> str:
        import numpy as np

        ids = [self._sp.bos_id()] + self._sp.EncodeAsIds(text) + [self._sp.eos_id()]
        outs = self._session.run(None, {self._input: np.array([ids], dtype=np.int64)})
        pre, post, cap, seg = (out[0].tolist() for out in outs[:4])
        pieces = [self._sp.IdToPiece(i) for i in ids]
        # Срезаем служебные токены начала и конца вместе с их предсказаниями.
        return assemble(pieces[1:-1], pre[1:-1], post[1:-1], cap[1:-1], seg[1:-1])


def load(models_dir: Path, progress=None) -> Punctuator:
    """Готовый пунктуатор. Весов нет — скачает их, докладывая долю в `progress`."""
    return Punctuator(ensure_weights(models_dir, progress))
