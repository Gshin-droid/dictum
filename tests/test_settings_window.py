"""Окно настроек: проверяем не вид, а что нажатия доходят до программы.

Вид проверяется глазами — тесты экрана не видят, и на этом уже обжигались.
Зато нажатие можно нажать: окно, которое красиво выглядит и ничего не
переключает, хуже отсутствующего.

Окно настоящее, а не поддельное: подделка проверяла бы саму себя. Tk на машине
без рабочего стола не поднимется — тогда проверка пропускается, а не падает.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

tk = pytest.importorskip("tkinter")


СОСТОЯНИЕ = {
    "модель": "gigaam-multilingual-ctc",
    "модели": [("gigaam-v3-e2e-rnnt", "Русский"),
               ("gigaam-multilingual-ctc", "Многоязычная")],
    "знаки": True,
    "клавиша": "F8",
    "минуты": 5,
    "варианты_минут": [2, 5, 10],
    "образцы": True,
    "язык": "ru",
    "языки": [("ru", "Русский"), ("kk", "Қазақша")],
}


def все_виджеты(корень):
    """Все виджеты окна вглубь: раскладка ещё поменяется, а проверка не должна."""
    for ребёнок in корень.winfo_children():
        yield ребёнок
        yield from все_виджеты(ребёнок)


@pytest.fixture
def окно():
    import messages
    import voice_dialogs

    messages.set_language("ru")
    try:
        root = tk.Tk()
    except tk.TclError as беда:  # нет рабочего стола — проверять нечем
        pytest.skip(f"Tk не поднялся: {беда}")
    root.withdraw()
    нажато = []
    voice_dialogs.show_settings(
        root,
        читать=lambda: dict(СОСТОЯНИЕ),
        on_model=lambda имя: нажато.append(("модель", имя)),
        on_punctuate=lambda да: нажато.append(("знаки", да)),
        on_hotkey=lambda: нажато.append(("клавиша",)),
        on_minutes=lambda мин: нажато.append(("минуты", мин)),
        on_samples=lambda да: нажато.append(("образцы", да)),
        on_language=lambda код: нажато.append(("язык", код)),
        on_dictionary=lambda: нажато.append(("словарь",)),
        on_log=lambda: нажато.append(("журнал",)),
    )
    win = voice_dialogs._окно_настроек
    yield win, нажато, voice_dialogs
    if win.winfo_exists():
        win.destroy()
    root.destroy()
    voice_dialogs._окно_настроек = None


def нажать(окно, подпись: str) -> bool:
    """Нажимает виджет с такой подписью. Ответ — нашёлся ли он вообще."""
    for виджет in все_виджеты(окно):
        try:
            if виджет.cget("text") == подпись:
                виджет.invoke()
                return True
        except tk.TclError:  # у рамок и подписей нажимать нечего
            continue
    return False


@pytest.mark.parametrize("подпись, ожидание", [
    ("Русский", ("модель", "gigaam-v3-e2e-rnnt")),
    ("Изменить", ("клавиша",)),
    ("2 мин", ("минуты", 2)),
    ("Сохранять записи на диск", ("образцы", False)),
    ("Словарь замен", ("словарь",)),
    ("Показать журнал", ("журнал",)),
])
def test_nazhatie_dohodit(окно, подпись, ожидание):
    win, нажато, _ = окно
    assert нажать(win, подпись), f"в окне нет ничего с подписью «{подпись}»"
    assert ожидание in нажато, f"нажали «{подпись}», а программа этого не увидела: {нажато}"


def test_znaki_prepinaniya_pryachutsya(окно):
    """Русская модель ставит знаки сама: переключателя быть не должно вовсе.

    «знаки»: None и означает «раздела не нужно». Показанный переключатель обещал
    бы то, чего программа не делает.
    """
    win, _нажато, voice_dialogs = окно
    подписи = []
    for виджет in все_виджеты(win):
        try:
            подписи.append(виджет.cget("text"))
        except tk.TclError:
            pass
    assert "Расставлять знаки препинания" in подписи

    win.destroy()
    voice_dialogs._окно_настроек = None
    без_знаков = dict(СОСТОЯНИЕ, знаки=None)
    root = win.master
    voice_dialogs.show_settings(
        root, читать=lambda: без_знаков,
        on_model=lambda *_: None, on_punctuate=lambda *_: None, on_hotkey=lambda *_: None,
        on_minutes=lambda *_: None, on_samples=lambda *_: None, on_language=lambda *_: None,
        on_dictionary=lambda *_: None, on_log=lambda *_: None,
    )
    второе = voice_dialogs._окно_настроек
    подписи = []
    for виджет in все_виджеты(второе):
        try:
            подписи.append(виджет.cget("text"))
        except tk.TclError:
            pass
    assert "Расставлять знаки препинания" not in подписи
    второе.destroy()


def test_vtoroe_okno_ne_otkryvaetsya(окно):
    """Открыть настройки дважды — получить два окна, которые спорят друг с другом."""
    win, _нажато, voice_dialogs = окно
    voice_dialogs.show_settings(
        win.master, читать=lambda: dict(СОСТОЯНИЕ),
        on_model=lambda *_: None, on_punctuate=lambda *_: None, on_hotkey=lambda *_: None,
        on_minutes=lambda *_: None, on_samples=lambda *_: None, on_language=lambda *_: None,
        on_dictionary=lambda *_: None, on_log=lambda *_: None,
    )
    assert voice_dialogs._окно_настроек is win, "открылось второе окно настроек"


def test_okna_pod_svoim_znachkom():
    """Без своего значка Tk подставляет перо Tcl/Tk, и у программы два лица:
    микрофон в лотке и чужое перо на окнах справки и настроек."""
    import voice_window

    try:
        root = tk.Tk()
    except tk.TclError as беда:
        pytest.skip(f"Tk не поднялся: {беда}")
    root.withdraw()
    try:
        assert voice_window.set_app_icon(root) is True
    finally:
        root.destroy()
