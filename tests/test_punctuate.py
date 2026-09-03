"""Пунктуатор: сборка строки из предсказаний модели и обе проверки."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("punctuate", ROOT / "punctuate.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["punctuate"] = module
    spec.loader.exec_module(module)
    return module


pn = _load()

NO_CAP = [False] * 16


def test_sobiraet_frazu_so_znakami():
    """Точка после последнего слова и заглавная в начале — обычный случай."""
    pieces = ["▁рахмет", "▁сізге"]
    pre = [0, 0]
    post = [0, pn.POST_LABELS.index(".")]
    # Регистр предсказан на каждую букву куска, и отсчёт идёт от начала куска
    # вместе со значком «▁». Значит, для «▁рахмет» заглавная — под номером 1.
    cap = [[False, True] + [False] * 14, NO_CAP]
    seg = [False, True]
    assert pn.assemble(pieces, pre, post, cap, seg) == "Рахмет сізге."


def test_znak_na_golom_probele_otbrasyvaetsya_a_probel_ostayotsya():
    """Разбивщик выдаёт «▁» без единой буквы. Знак на нём цеплять не к чему —
    он вылезал как «.Рахмет». Но границу слова этот токен означает, и потеря
    пробела склеивала слова: «пікірге ие» → «пікіргеие»."""
    pieces = ["▁", "рахмет", "▁", "сізге"]
    pre = [0, 0, 0, 0]
    post = [pn.POST_LABELS.index("."), 0, pn.POST_LABELS.index(","), 0]
    cap = [NO_CAP, NO_CAP, NO_CAP, NO_CAP]
    seg = [False, False, False, False]
    assert pn.assemble(pieces, pre, post, cap, seg) == "рахмет сізге"


def test_guard_propuskaet_kogda_slova_te_zhe():
    assert pn.guard("рахмет сізге", "Рахмет сізге.") == "Рахмет сізге."


def test_guard_vozvrashchaet_ishodnik_kogda_slova_izmenilis():
    """Склеенные слова — брак, знаки не нужны такой ценой."""
    assert pn.guard("пікірге ие", "Пікіргеие.") == "пікірге ие"


def test_guard_vozvrashchaet_ishodnik_kogda_tekst_nachinaetsya_so_znaka():
    """Слова целы, а текст испорчен. Первая проверка это пропускает."""
    assert pn.guard("рахмет сізге", ".Рахмет сізге") == "рахмет сізге"


def test_guard_ne_putaet_registr_i_znaki_so_smenoy_slov():
    assert pn.guard("бүгін ауа райы қандай", "Бүгін ауа райы қандай?") == \
        "Бүгін ауа райы қандай?"


def test_korotkiy_tekst_odnoy_gruppoy():
    words = ["бір", "екі", "үш"]
    assert pn.split_words(words, lambda w: 1, limit=10) == [words]


def test_dlinnyy_tekst_rezhetsya_po_predelu():
    """Предел 6 подслов, запас 2 на служебные токены — значит по 4 слова."""
    words = [f"сөз{i}" for i in range(9)]
    groups = pn.split_words(words, lambda w: 1, limit=6)
    assert [len(g) for g in groups] == [4, 4, 1]
    assert [w for g in groups for w in g] == words


def test_odno_ochen_dlinnoe_slovo_ne_teryaetsya():
    """Слово, которое само не влезает в окно, всё равно должно попасть в вывод —
    иначе проверка целости отбросит знаки на всём тексте."""
    groups = pn.split_words(["коротко", "оченьдлинное"], lambda w: 1 if w == "коротко" else 99,
                            limit=6)
    assert [w for g in groups for w in g] == ["коротко", "оченьдлинное"]


WEIGHTS = ROOT / "models" / pn.MODEL_DIR
HAS_WEIGHTS = (WEIGHTS / pn.ONNX_NAME).exists()
skip_bez_vesov = pytest.mark.skipif(
    not HAS_WEIGHTS, reason=f"нет весов в {WEIGHTS} — 233 МБ, качаются отдельно"
)


@skip_bez_vesov
def test_kazahskiy_vopros_poluchaet_znak():
    """«ма» — вопросительная частица казахского. Знак должен встать после неё."""
    p = pn.load(ROOT / "models")
    got = p.apply("бұл модель қазақ тілінде тыныс белгілерін қоя ала ма соны тексеріп көрейік")
    assert got == "Бұл модель қазақ тілінде тыныс белгілерін қоя ала ма? Соны тексеріп көрейік."


@skip_bez_vesov
def test_slova_ne_menyayutsya_na_dlinnom_tekste():
    """Главный инвариант на тексте длиннее окна модели."""
    p = pn.load(ROOT / "models")
    text = " ".join(["бүгінгі дәрісте біз ықтималдық теориясының негізгі ұғымдарын "
                     "қарастырамыз"] * 12)
    got = p.apply(text)
    assert pn._words(got) == pn._words(text)


@skip_bez_vesov
def test_pustaya_stroka_prohodit_naskvoz():
    p = pn.load(ROOT / "models")
    assert p.apply("") == ""
    assert p.apply("   ") == "   "


@skip_bez_vesov
def test_abzatsy_sohranyayutsya():
    """Расшифровка файла отдаёт абзацы через пустую строку — их нельзя схлопывать."""
    p = pn.load(ROOT / "models")
    got = p.apply("рахмет сізге\n\nмен келдім")
    assert got.count("\n\n") == 1


# ---- дефисы ----


@pytest.mark.parametrize(
    "bylo, stalo",
    [
        ("какие то переменные", "какие-то переменные"),
        ("он что то докачивает", "он что-то докачивает"),
        ("кто то из мастеров", "кто-то из мастеров"),
        ("где то читал", "где-то читал"),
        ("дай что нибудь", "дай что-нибудь"),
        ("из за наших действий", "из-за наших действий"),
        ("из под стола", "из-под стола"),
        ("все таки решил", "все-таки решил"),
        ("чуть чуть поработаем", "чуть-чуть поработаем"),
        ("во первых это дорого", "во-первых это дорого"),
        ("кое что забыл", "кое-что забыл"),
        ("говорит по русски", "говорит по-русски"),
        ("говорит по казахски", "говорит по-казахски"),
        ("ходит туда сюда", "ходит туда-сюда"),
    ],
)
def test_defis_vozvrashchaetsya(bylo, stalo):
    assert pn.hyphens(bylo) == stalo


@pytest.mark.parametrize(
    "tekst",
    [
        # «то» указательное: приклеить его к предыдущему слову — испортить фразу.
        "показало то что переменные создаются",
        "для проверки то что в другом окне",
        # «либо» здесь союз, а не суффикс. Ради этого случая правила по суффиксу и нет.
        "стрижка либо укладка",
        "договоримся либо дальше буду я вести",
        # «по» с местоимением в дательном — предлог, а не наречие «по-моему».
        "по моему мнению это дорого",
        "пошли по другому пути",
        "действовал по своему усмотрению",
    ],
)
def test_vernyy_tekst_ne_portitsya(tekst):
    assert pn.hyphens(tekst) == tekst


def test_registr_sohranyaetsya():
    """Подменяется только пробел, буквы остаются как были."""
    assert pn.hyphens("Кто то из мастеров") == "Кто-то из мастеров"


def test_uzhe_skleennoe_ne_trogaem():
    assert pn.hyphens("из-за наших действий") == "из-за наших действий"


def test_znaki_ryadom_ne_meshayut():
    assert pn.hyphens("если что то, не то — скажи") == "если что-то, не то — скажи"
