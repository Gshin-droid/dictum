"""Окна «О программе» и «Справка». Рисует их сам, системными не обходится.

Почему не всплывающая подсказка. «О программе» показывалось через icon.notify —
это системная подсказка у часов, и размер ей задаёт Windows: длинный текст она
режет молча, без многоточия и без прокрутки. Человек видел обрывок и не знал,
что там было дальше.

Почему не блокнот. Справка писалась файлом рядом с программой и открывалась
блокнотом. Работало, но выглядело как временный файл: чужое окно, чужой шрифт,
одна простыня текста без оглавления.

Отделка та же, что у капсулы диктовки: тот же тёмный фон и тот же синий акцент,
чтобы вся программа выглядела одним целым. Цвета продублированы здесь, а не
взяты из voice_window: там они означают состояние записи (волна, точка), тут —
отделку окна, и общее имя связало бы два несвязанных смысла.

Рисовать отсюда можно только из потока Tk. Меню значка живёт в другом потоке,
поэтому зовёт эти окна через VoiceWindow.request.
"""

import re
import tkinter as tk
import webbrowser

from messages import t

BG = "#0e0e10"          # тело окна
PANEL = "#1c1c20"       # боковая полоса со списком разделов
PICKED = "#20304a"      # выбранный раздел: синева, а не сплошная заливка акцентом
TEXT = "#d8d8dc"
DIM = "#8e8e93"
CHIP = "#2c2c2e"        # плашка клавиши
SEP = "#2a2a2e"
ACCENT = "#0a84ff"
FONT = "Segoe UI"
MONO = "Consolas"       # только для строк-столбцов, см. _fill


def dark_titlebar(win) -> None:
    """Просит Windows покрасить рамку окна тёмным. Не умеет — останется светлой.

    Своими силами это не сделать: рамку рисует система, а не Tk. Отказ не
    страшен — окно просто получит светлый заголовок над тёмным телом.
    """
    import ctypes

    DWMWA_USE_IMMERSIVE_DARK_MODE = 20
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetAncestor(win.winfo_id(), 2)  # GA_ROOT
        value = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(value), ctypes.sizeof(value)
        )
    except Exception:  # старая Windows или нет dwmapi — не повод не показывать окно
        pass


def _window(root, title: str, width: int, height: int):
    """Общая заготовка: тёмное окно по центру экрана, Esc закрывает.

    Наверх выносим только на миг: окно зовут из меню значка, и без этого оно
    открывается позади активного приложения. Оставлять его поверх всех нельзя —
    справку читают, поглядывая в другое окно.
    """
    win = tk.Toplevel(root)
    win.title(title)
    win.configure(bg=BG)
    x = (win.winfo_screenwidth() - width) // 2
    y = (win.winfo_screenheight() - height) // 2
    win.geometry(f"{width}x{height}+{x}+{y}")
    win.minsize(width, height)
    dark_titlebar(win)
    win.bind("<Escape>", lambda _e: win.destroy())
    win.attributes("-topmost", True)
    win.after(400, lambda: win.winfo_exists() and win.attributes("-topmost", False))
    win.lift()
    win.focus_force()
    return win


def _scrollbar(parent):
    """Полоса прокрутки в цветах окна.

    Обычной tk.Scrollbar на Windows цвета не задать: её рисует система по своей
    теме, и на тёмном окне она остаётся светло-серой заплаткой. Цвета слушает
    только тема «clam» — она рисует полосу сама, поэтому берём ttk с ней.
    """
    from tkinter import ttk

    style = ttk.Style(parent)
    try:
        style.theme_use("clam")
    except tk.TclError:  # темы нет — пусть будет системная, это лучше, чем без полосы
        return tk.Scrollbar(parent, orient="vertical")
    style.configure(
        "Dictum.Vertical.TScrollbar", background=CHIP, troughcolor=BG, bordercolor=BG,
        darkcolor=CHIP, lightcolor=CHIP, arrowcolor=DIM, relief="flat", borderwidth=0,
    )
    style.map("Dictum.Vertical.TScrollbar", background=[("active", DIM)])
    return ttk.Scrollbar(parent, orient="vertical", style="Dictum.Vertical.TScrollbar")


def _link(parent, text: str, url: str):
    """Подпись, которая открывает ссылку в браузере."""
    label = tk.Label(parent, text=text, bg=BG, fg=ACCENT, font=(FONT, 9), cursor="hand2")
    label.bind("<Button-1>", lambda _e: webbrowser.open(url))
    return label


# --- О программе ---------------------------------------------------------


def show_about(root, *, app: str, version: str, tagline: str, model: str, hotkey: str,
               punctuation: bool, language: str, author: str, url: str) -> None:
    """Окно с данными о программе. Все надписи приходят готовыми, отсюда не считаются."""
    win = _window(root, f"{t('menu.about')} — {app}", 460, 372)

    шапка = tk.Frame(win, bg=BG)
    шапка.pack(fill="x", padx=28, pady=(26, 18))
    tk.Frame(шапка, bg=ACCENT, width=3).pack(side="left", fill="y", padx=(0, 14))
    подписи = tk.Frame(шапка, bg=BG)
    подписи.pack(side="left", anchor="w")
    tk.Label(подписи, text=f"{app}  {version}", bg=BG, fg=TEXT,
             font=(FONT, 17, "bold")).pack(anchor="w")
    tk.Label(подписи, text=tagline, bg=BG, fg=DIM, font=(FONT, 10)).pack(anchor="w")

    строки = tk.Frame(win, bg=BG)
    строки.pack(fill="x", padx=28)
    пары = [
        (t("about.model"), model, False),
        (t("about.hotkey"), hotkey, True),
        (t("about.punctuation"), t("about.on") if punctuation else t("about.off"), False),
        (t("menu.interface_language"), language, False),
    ]
    for ряд, (имя, значение, плашка) in enumerate(пары):
        tk.Label(строки, text=имя, bg=BG, fg=DIM, font=(FONT, 10),
                 anchor="w").grid(row=ряд, column=0, sticky="w", pady=4)
        if плашка:  # клавишу показываем как кнопку, а не как слово в строке
            рамка = tk.Frame(строки, bg=CHIP)
            рамка.grid(row=ряд, column=1, sticky="w", padx=(24, 0), pady=4)
            tk.Label(рамка, text=значение, bg=CHIP, fg=TEXT,
                     font=(FONT, 9, "bold")).pack(padx=9, pady=1)
        else:
            tk.Label(строки, text=значение, bg=BG, fg=TEXT, font=(FONT, 10),
                     anchor="w").grid(row=ряд, column=1, sticky="w", padx=(24, 0), pady=4)

    tk.Label(win, text=t("about.privacy"), bg=BG, fg=TEXT, font=(FONT, 10),
             justify="left", wraplength=390).pack(anchor="w", padx=28, pady=(22, 0))

    tk.Frame(win, bg=SEP, height=1).pack(fill="x", padx=28, pady=(22, 0))

    низ = tk.Frame(win, bg=BG)
    низ.pack(anchor="w", padx=28, pady=14)
    _link(низ, t("about.source"), f"https://{url}").pack(side="left")
    tk.Label(низ, text="·", bg=BG, fg=SEP, font=(FONT, 9)).pack(side="left", padx=8)
    tk.Label(низ, text=t("about.license"), bg=BG, fg=DIM, font=(FONT, 9)).pack(side="left")
    tk.Label(низ, text="·", bg=BG, fg=SEP, font=(FONT, 9)).pack(side="left", padx=8)
    tk.Label(низ, text=author, bg=BG, fg=DIM, font=(FONT, 9)).pack(side="left")


# --- Справка -------------------------------------------------------------


def sections(text: str) -> list[tuple[str, str]]:
    """Режет справку на разделы. Заголовок — строка заглавными от левого края.

    Разметки в справке нет и не надо: тот же текст уходит файлом «Прочти
    меня.txt» в переносную копию, где никакой разметки не прочтут. Заглавные
    буквы и отступ в два пробела там уже есть — по ним и режем.

    Первый кусок идёт без заголовка (это название и строка про то, что
    программа делает) — ему заголовком служит собственная первая строка.
    """
    куски: list[tuple[str, list[str]]] = []
    заголовок, тело = None, []
    for строка in text.strip("\n").split("\n"):
        свой = (строка and not строка.startswith(" ")
                and строка == строка.upper()
                and any(знак.isalpha() for знак in строка))
        if свой:
            if заголовок or any(s.strip() for s in тело):
                куски.append((заголовок, тело))
            заголовок, тело = строка, []
        else:
            тело.append(строка)
    if заголовок or any(s.strip() for s in тело):
        куски.append((заголовок, тело))

    готово = []
    for имя, строки in куски:
        if имя is None:  # вступление: заголовком служит его же первая строка
            непустые = [s for s in строки if s.strip()]
            имя, строки = (непустые[0] if непустые else "…"), строки[1:]
        # у тела отступ в два пробела на каждой строке — в окне он не нужен
        обрезано = [s[2:] if s.startswith("  ") else s for s in строки]
        готово.append((имя, "\n".join(обрезано).strip("\n")))
    return готово


СТОЛБЦЫ = re.compile(r"\S {3,}\S")  # «F8            начать запись» — выровнено пробелами


def _monospace(строка: str) -> bool:
    """Строку надо показать шрифтом пишущей машинки?

    Всё моноширинным нельзя — обычные абзацы выглядят распечаткой из терминала,
    и справка от этого кажется отпиской. Всё пропорциональным тоже нельзя: в
    справке есть столбцы, выровненные пробелами, и они разъезжаются.

    Признак берём с самой строки, а не из разметки: разметки в справке нет и не
    надо — тот же текст уходит файлом «Прочти меня.txt» в переносную копию.
    Три пробела подряд внутри строки и отступ в начале бывают только там, где
    что-то выравнивали руками.
    """
    return bool(строка.startswith(" ") or СТОЛБЦЫ.search(строка))


def _fill(тело, содержимое: str) -> None:
    """Кладёт текст раздела в окно, решая шрифт для каждой строки."""
    тело.config(state="normal")
    тело.delete("1.0", "end")
    for строка in содержимое.split("\n"):
        тело.insert("end", строка + "\n", ("столбцы",) if _monospace(строка) else ())
    тело.config(state="disabled")
    тело.yview_moveto(0)


def show_help(root, text: str) -> None:
    """Справка: разделы слева, выбранный текст справа."""
    разделы = sections(text)
    win = _window(root, f"{t('menu.help')} — Dictum", 960, 620)

    слева = tk.Frame(win, bg=PANEL, width=370)
    слева.pack(side="left", fill="y")
    слева.pack_propagate(False)  # иначе полоса сожмётся по самому длинному разделу
    список = tk.Listbox(
        слева, bg=PANEL, fg=DIM, font=(FONT, 9), borderwidth=0, highlightthickness=0,
        selectbackground=PICKED, selectforeground="#ffffff", activestyle="none",
        exportselection=False,  # иначе выделение слетает при клике в текст справа
    )
    список.pack(fill="both", expand=True, padx=(14, 10), pady=16)
    for имя, _ in разделы:
        список.insert("end", f"  {имя}")

    справа = tk.Frame(win, bg=BG)
    справа.pack(side="left", fill="both", expand=True)
    название = tk.Label(справа, text="", bg=BG, fg=ACCENT, font=(FONT, 13, "bold"),
                        anchor="w", justify="left", wraplength=520)
    название.pack(fill="x", padx=26, pady=(20, 10))

    полоса = _scrollbar(справа)
    полоса.pack(side="right", fill="y", padx=(0, 8), pady=(0, 18))
    # spacing не трогаем: в справке абзацы уже разделены пустой строкой, а
    # добавка сверху и снизу к каждой строке делала из переноса вид нового абзаца
    тело = tk.Text(справа, bg=BG, fg=TEXT, font=(FONT, 10), wrap="word", borderwidth=0,
                   highlightthickness=0, padx=26, pady=0, yscrollcommand=полоса.set)
    тело.tag_configure("столбцы", font=(MONO, 10))
    тело.pack(side="left", fill="both", expand=True, pady=(0, 18))
    полоса.config(command=тело.yview)

    def показать(_event=None) -> None:
        выбор = список.curselection()
        имя, содержимое = разделы[выбор[0] if выбор else 0]
        название.config(text=имя)
        _fill(тело, содержимое)

    список.bind("<<ListboxSelect>>", показать)
    # колесо крутит текст, а не список: на Windows событие приходит окну с
    # фокусом, а фокус после клика по разделу остаётся на списке
    win.bind("<MouseWheel>", lambda e: тело.yview_scroll(-e.delta // 120, "units"))
    список.selection_set(0)
    показать()


def demo() -> None:
    """Самопроверка глазами: открывает оба окна разом, без микрофона и модели."""
    import help_text

    разделы = sections(help_text.TEXT)
    assert len(разделы) >= 10, f"разделов нашлось всего {len(разделы)} — разбор промахнулся"
    assert all(имя and тело for имя, тело in разделы), "есть раздел без заголовка или пустой"

    root = tk.Tk()
    root.withdraw()
    show_about(root, app="Dictum", version="1.2.0", tagline="голосовая диктовка",
               model="Русский (GigaAM v3)", hotkey="F8", punctuation=True,
               language="Русский", author="Gshin-droid", url="github.com/Gshin-droid/dictum")
    show_help(root, help_text.TEXT)
    print(f"demo: разделов {len(разделы)} — " + ", ".join(имя for имя, _ in разделы[:3]) + "…")
    root.mainloop()


if __name__ == "__main__":
    demo()
