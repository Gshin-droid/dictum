"""Разбор справки на разделы и выбор шрифта для строки.

Окна тут не открываются: проверяется чистая часть — то, что можно посчитать.
Как это выглядит на экране, тестом не увидеть, для этого есть voice_dialogs.demo.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(имя: str):
    spec = importlib.util.spec_from_file_location(имя, ROOT / f"{имя}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[имя] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def vd():
    _load("messages")
    return _load("voice_dialogs")


@pytest.fixture(scope="module")
def справка():
    return _load("help_text").TEXT


def test_razdely_naydeny(vd, справка):
    разделы = vd.sections(справка)
    имена = [имя for имя, _ in разделы]
    assert len(разделы) >= 12, f"нашлось всего {len(разделы)}: {имена}"
    assert "КАК ПОЛЬЗОВАТЬСЯ" in имена
    assert "ЖУРНАЛ БУФЕРА ОБМЕНА WINDOWS" in имена, "заголовок с латинским словом потерялся"


def test_pustyh_razdelov_net(vd, справка):
    """Раздел без текста — это промах разбора, а на экране он выглядит поломкой."""
    for имя, тело in vd.sections(справка):
        assert имя.strip(), "раздел без заголовка"
        assert тело.strip(), f"раздел «{имя}» пустой"


def test_nichego_ne_poteryalos(vd, справка):
    """Главная проверка: разбор режет текст, а не выбрасывает куски.

    Раздел, который тихо не попал в окно, никак себя не проявит — человек
    просто не найдёт нужного и решит, что этого в программе нет.
    """
    собрано = "\n".join(имя + "\n" + тело for имя, тело in vd.sections(справка))
    потеряно = [строка.strip() for строка in справка.strip().split("\n")
                if строка.strip() and строка.strip() not in собрано]
    assert not потеряно, f"строки не попали ни в один раздел: {потеряно[:3]}"


def test_otstup_snyat(vd, справка):
    """В исходнике тело каждого раздела отбито двумя пробелами — в окне они лишние."""
    разделы = dict(vd.sections(справка))
    первая = разделы["КАК ЗАПУСТИТЬ"].split("\n")[0]
    assert not первая.startswith(" "), f"отступ остался: «{первая}»"


def test_zagolovok_ne_putaetsya_s_tekstom(vd):
    """Строка заглавными внутри абзаца заголовком не считается — она с отступом."""
    разделы = vd.sections("Название\n\nвступление\n\nРАЗДЕЛ\n  текст\n  И ВОТ ЭТО ТОЖЕ ТЕКСТ\n")
    имена = [имя for имя, _ in разделы]
    assert имена == ["Название", "РАЗДЕЛ"], имена
    assert "И ВОТ ЭТО ТОЖЕ ТЕКСТ" in разделы[1][1]


def test_zakaz_okna_dohodit_do_tk():
    """Меню значка живёт в чужом потоке и рисовать не имеет права.

    Проверяем сам мост: заказ кладётся в очередь, а выполняется тогда, когда за
    ним придёт поток Tk. Без этого программа падала бы примерно через раз.
    """
    _load("messages")
    окно = _load("voice_window")

    win = object.__new__(окно.VoiceWindow)
    win._requests = __import__("collections").deque()
    win.root = "корень"
    сделано = []

    win.request(lambda root: сделано.append(root))
    assert сделано == [], "заказ выполнился прямо в чужом потоке"

    win._serve_requests()
    assert сделано == ["корень"], "поток Tk до заказа не добрался"


def test_slomannoe_okno_ne_ronyaet_programmu():
    """Окно не должно уносить с собой диктовку: она важнее окна."""
    _load("messages")
    окно = _load("voice_window")

    class Запись:
        def __init__(self):
            self.сказано = []

        def announce(self, текст, _сек=None):
            self.сказано.append(текст)

    win = object.__new__(окно.VoiceWindow)
    win._requests = __import__("collections").deque()
    win.root = None
    win.rec = Запись()

    def падает(_root):
        raise RuntimeError("не смогло нарисоваться")

    win.request(падает)
    win._serve_requests()  # падения быть не должно

    assert win.rec.сказано, "поломка окна прошла молча"


def test_monoshirinnyy_tolko_gde_nado(vd):
    assert vd._monospace("F8            начать запись"), "столбцы разъедутся"
    assert vd._monospace("    гугл = Google"), "пример с отступом — тоже разметка пробелами"
    assert not vd._monospace("Курсор нужно поставить в поле для ввода до нажатия F8.")
    assert not vd._monospace("")
