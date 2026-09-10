"""Общий список сообщений: запасной вариант, подстановка, целость ключей."""

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("messages", ROOT / "messages.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["messages"] = module
    spec.loader.exec_module(module)
    return module


# Ключ ищем как строковый литерал нужного вида, а не по вызову `t(...)`: два
# ключа стоят внутри условия — `t("notice.punct_on" if value else
# "notice.punct_off")`, и поиск по вызову видел только первый из двух.
КЛЮЧ = re.compile(r"""["']((?:menu|notice|about|error|settings|capsule)\.\w+)["']""")


def _klyuchi_iz_koda() -> set:
    """Ищет ключи во всех файлах программы, а не в перечисленных поимённо.

    Список имён пришлось бы дополнять при каждом новом файле, а забытое имя
    делает проверку тихо бесполезной: ключи оттуда выглядели бы мёртвыми.
    Сам messages.py пропускаем — там лежат все ключи разом, и с ним проверка
    «ключ нигде не используется» не поймала бы ничего.
    """
    найдено = set()
    for путь in ROOT.glob("*.py"):
        if путь.name == "messages.py":
            continue
        найдено |= set(КЛЮЧ.findall(путь.read_text(encoding="utf-8")))
    return найдено


@pytest.fixture
def ms():
    module = _load()
    yield module
    module.set_language(module.DEFAULT)


def test_russkiy_po_umolchaniyu(ms):
    assert ms.language() == "ru"
    assert ms.t("menu.help") == "Справка"


def test_podstanovka(ms):
    assert ms.t("menu.minutes", minutes=5) == "5 мин"


def test_bez_perevoda_beryotsya_russkiy(ms):
    """Недоделанный перевод портит вид, но не ломает программу.

    Ключ подкладываем свой: опереться на настоящий нельзя — сегодня он без
    перевода, завтра переведён, и проверка запаса тихо перестанет его проверять.
    """
    ms.TEXTS["menu.выдуманный"] = {"ru": "Выдуманный пункт"}
    ms.set_language("kk")
    assert ms.t("menu.выдуманный") == "Выдуманный пункт"


def test_perevod_ispolzuetsya_kogda_est(ms):
    ms.set_language("kk")
    assert ms.t("menu.help") == "Анықтама"


def test_neizvestnyy_yazyk_ne_menyaet_tekushchiy(ms):
    ms.set_language("kk")
    assert ms.set_language("эльфийский") == "kk", "незнакомый код не должен ничего менять"


def test_neizvestnyy_klyuch_vozvrashchaet_sam_klyuch(ms):
    """Лучше показать имя ключа, чем пустоту: поломка будет видна сразу."""
    assert ms.t("такого.нет") == "такого.нет"


def test_slomannaya_podstanovka_ne_ronyaet(ms):
    """Не хватило значения — отдаём шаблон как есть, а не падаем посреди диктовки."""
    assert "{minutes}" in ms.t("menu.minutes")


def test_u_kazhdogo_klyucha_est_russkiy(ms):
    """Русский — запасной для всех, поэтому пропускать его нельзя."""
    без_русского = [k for k, v in ms.TEXTS.items() if not v.get("ru")]
    assert not без_русского, f"без русского текста: {без_русского}"


def test_polya_podstanovki_sovpadayut_mezhdu_yazykami(ms):
    """Перевод, потерявший {name}, покажет фразу без имени файла и никто не заметит."""
    for ключ, варианты in ms.TEXTS.items():
        поля = {язык: set(re.findall(r"\{(\w+)\}", текст)) for язык, текст in варианты.items()}
        эталон = поля.get(ms.FALLBACK, set())
        for язык, набор in поля.items():
            assert набор == эталон, f"{ключ}: у «{язык}» поля {набор}, у русского {эталон}"


def test_vse_yazyki_iz_spiska_izvestny(ms):
    """Колонка перевода без языка в списке в меню не появится и останется мёртвой."""
    for ключ, варианты in ms.TEXTS.items():
        чужие = set(варианты) - set(ms.LANGUAGES)
        assert not чужие, f"{ключ}: языки вне списка — {чужие}"


def test_vse_klyuchi_iz_koda_est_v_spiske(ms):
    """Опечатка в ключе показала бы на экране «menu.hepl» вместо надписи.

    Проверка замкнута на самих файлах: список ключей в коде обязан быть
    подмножеством списка сообщений, законных исключений у этого нет.
    """
    ispolzovano = _klyuchi_iz_koda()
    assert ispolzovano, "ни одного вызова не нашлось — проверка смотрит не туда"
    poteryannye = sorted(ispolzovano - set(ms.TEXTS))
    assert not poteryannye, f"ключи есть в коде, но не в списке: {poteryannye}"


def test_neispolzovannye_klyuchi_nazvany(ms):
    """Мёртвая строка в списке — не ошибка, но знать о ней надо.

    Она либо ждёт своего пункта меню, либо осталась от удалённого — второе
    молча копится и превращает список в свалку.
    """
    ispolzovano = _klyuchi_iz_koda()
    lishnie = sorted(set(ms.TEXTS) - ispolzovano)
    assert not lishnie, f"строки есть в списке, но нигде не используются: {lishnie}"


# Ключи, которым перевод ещё не сделан. Пустой список — норма; строка здесь
# означает «знаем и ждём». Список нужен затем, что без него непереведённая
# надпись молчит: программа честно показывает русский запас, и человек с
# казахским интерфейсом видит русское слово, а проверки довольны.
# Так и вышло 10.09.2026 с надписями капсулы — заметил пользователь на живой
# диктовке. Жёстко запретить нельзя: новый ключ без перевода — законный случай.
БЕЗ_ПЕРЕВОДА: set[str] = set()


def test_kazahskiy_est_u_vseh_klyuchey_krome_nazvannyh(ms):
    """Пропуск перевода обязан быть назван поимённо, а не обнаружен на экране."""
    молчат = {k for k, v in ms.TEXTS.items() if not v.get("kk", "").strip()}
    забыли = sorted(молчат - БЕЗ_ПЕРЕВОДА)
    assert not забыли, (
        "нет казахского перевода: " + ", ".join(забыли) +
        ".\nПеревести или внести ключ в БЕЗ_ПЕРЕВОДА, чтобы пропуск был виден."
    )
    лишние = sorted(БЕЗ_ПЕРЕВОДА - молчат)
    assert not лишние, f"перевод уже есть, убрать из БЕЗ_ПЕРЕВОДА: {лишние}"
