"""Окно диктовки: стеклянная капсула с волной громкости и подсказками клавиш.

Про Whisper и микрофон не знает ничего. Получает объект записи и только рисует
его состояние, опрашивая каждые 50 мс. В покое окно скрыто, при диктовке
всплывает снизу по центру экрана.

От объекта записи нужны: state ("idle" / "recording" / "busy"), levels
(история громкости, числа 0..1), notice (сообщение и до какого времени его
показывать), методы toggle() и cancel().
"""

import threading
import time
import tkinter as tk

W, H = 560, 132
FOOTER = 44  # высота нижней служебной полосы, она непрозрачная
RADIUS = 26  # своё скругление: столько рисуем, когда режем углы сами
SYSTEM_RADIUS = 8  # столько кладёт Windows 11, под него подгоняем контур
BOTTOM_MARGIN = 110  # отступ от низа экрана, чтобы не лезть на панель задач
WAVE_STEP = 6  # шаг между чёрточками волны
WAVE_PAD = 26

KEY = "#010203"  # эти пиксели Windows заменяет размытым фоном
EDGE = "#5a5a63"
SOLID_BG = "#0e0e10"  # запасной фон, если размытие не включилось
FOOTER_BG = "#1c1c20"
TEXT = "#d8d8dc"
DIM = "#8e8e93"
CHIP_BG = "#2c2c2e"
SEP = "#48484a"
WAVE = {"idle": DIM, "recording": "#e8e8ea", "busy": "#0a84ff"}
DOT = {"idle": DIM, "recording": "#ff453a", "busy": "#0a84ff"}

BARS = (W - 2 * WAVE_PAD) // WAVE_STEP


def rounded(canvas, x1, y1, x2, y2, r, **kw):
    """Скруглённый прямоугольник: своего у Tk нет, собираем из сглаженного контура."""
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(pts, smooth=True, **kw)


def chip(canvas, x, y, text, *, font=("Segoe UI", 8), pad=6, tags=()):
    """Клавиша-чип: подпись в скруглённой плашке. Возвращает правый край."""
    tid = canvas.create_text(x + pad, y, text=text, anchor="w", fill="#e5e5e7", font=font, tags=tags)
    x1, y1, x2, y2 = canvas.bbox(tid)
    box = rounded(canvas, x1 - pad, y1 - 3, x2 + pad, y2 + 3, 5, fill=CHIP_BG, outline="", tags=tags)
    canvas.tag_lower(box, tid)
    return x2 + pad


def _hwnd(root) -> int:
    """Настоящее окно Windows под окном Tk."""
    import ctypes

    root.update_idletasks()
    return ctypes.windll.user32.GetAncestor(root.winfo_id(), 2)  # GA_ROOT


def prepare_window(root) -> None:
    """Не красть фокус (текст вставляется в прежнее окно) и не светиться в таскбаре."""
    import ctypes

    GWL_EXSTYLE, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW = -20, 0x08000000, 0x00000080
    hwnd = _hwnd(root)
    current = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    ctypes.windll.user32.SetWindowLongW(
        hwnd, GWL_EXSTYLE, current | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
    )


def round_corners(root) -> bool:
    """Просит Windows скруглить само окно. False — не умеет (это Windows 10).

    Скруглить своими силами не выходит, и это стоило одной неверной починки.
    Нарисованный на холсте контур углы не спасает: холст — картинка внутри окна,
    а окно прямоугольное, и размытый фон Windows кладёт за ним по всему
    прямоугольнику. Область окна (SetWindowRgn) тоже мимо: у «слоёных» окон,
    а наше именно такое, система область при отрисовке не смотрит — замер
    показал угловой пиксель одного цвета с областью и без неё.

    Остаётся попросить саму Windows. Она режет и окно, и свой размытый фон,
    да ещё со сглаживанием края, которого нам не получить: прозрачность по
    цвету-ключу полутонов не знает. Плата — радиус назначает система.
    """
    import ctypes

    DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND = 33, 2
    value = ctypes.c_int(DWMWCP_ROUND)
    try:
        code = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            _hwnd(root), DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(value), ctypes.sizeof(value)
        )
    except Exception:
        return False
    return code == 0  # S_OK


def enable_acrylic(root) -> bool:
    """Просит Windows рисовать за окном размытый фон. False — система не умеет."""
    import ctypes

    hwnd = _hwnd(root)
    try:
        class Accent(ctypes.Structure):
            _fields_ = [("state", ctypes.c_int), ("flags", ctypes.c_int),
                        ("gradient", ctypes.c_uint), ("anim", ctypes.c_int)]

        class Data(ctypes.Structure):
            _fields_ = [("attr", ctypes.c_int), ("data", ctypes.c_void_p),
                        ("size", ctypes.c_size_t)]

        accent = Accent(4, 2, 0x99101014, 0)  # ACRYLIC + тонировка AABBGGRR
        payload = Data(19, ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p),
                       ctypes.sizeof(accent))
        return bool(ctypes.windll.user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(payload)))
    except Exception:
        return False


class VoiceWindow:
    """Капсула диктовки. Живёт всё время работы программы, показывается по надобности."""

    def __init__(self, recorder, hotkey: str, on_files=None):
        self.rec = recorder
        self.hotkey = hotkey.upper()
        self.on_files = on_files  # куда отдать выбранные файлы на расшифровку
        self.should_quit = threading.Event()  # ставит меню в трее
        self._new_hotkey = None  # смена клавиши приходит из чужого потока, применяем в своём
        self._ask_file = False  # просьба показать выбор файла, тоже из чужого потока
        self.visible = False
        self.heights = [1.0] * BARS

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.geometry(self._geometry())
        self.canvas = tk.Canvas(self.root, width=W, height=H, bg=KEY, highlightthickness=0)
        self.canvas.pack()
        prepare_window(self.root)

        # Порядок не случаен: стекло допустимо только там, где система умеет
        # скруглить окно. Иначе размытый фон зальёт углы квадратом поверх любого
        # нарисованного контура — так и было до 1.1.2. Оговорка в правиле не
        # помогла бы: «and» делает эту пару неразрывной, забыть её нельзя.
        self.system_rounded = round_corners(self.root)
        self.glass = self.system_rounded and enable_acrylic(self.root)

        # со стеклом радиус диктует Windows, без него рисуем свой, задуманный
        self.radius = SYSTEM_RADIUS if self.glass else RADIUS
        # прозрачные углы нужны обеим отделкам: у стекла сквозь них видно размытие,
        # у матовой — рабочий стол, и только так своё скругление вообще видно
        self.root.config(bg=KEY)
        self.root.attributes("-transparentcolor", KEY)

        self._build()
        self.root.withdraw()
        self._tick()

    def _geometry(self) -> str:
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        return f"{W}x{H}+{(screen_w - W) // 2}+{screen_h - H - BOTTOM_MARGIN}"

    # --- отрисовка неподвижной части ------------------------------------

    def _build(self) -> None:
        c = self.canvas
        body = KEY if self.glass else SOLID_BG
        r = self.radius
        rounded(c, 1, 1, W - 1, H - 1, r, fill=body, outline=EDGE)
        # нижняя полоса непрозрачная: сквозь прозрачные пиксели мышь проваливается,
        # а «Стоп» и «Отмена» должны нажиматься
        rounded(c, 1, H - FOOTER, W - 1, H - 1, r, fill=FOOTER_BG, outline="")
        c.create_rectangle(1, H - FOOTER, W - 1, H - FOOTER + r, fill=FOOTER_BG, outline="")

        mid = (H - FOOTER) / 2 + 4
        self.bars = [
            c.create_line(WAVE_PAD + i * WAVE_STEP, mid - 1, WAVE_PAD + i * WAVE_STEP, mid + 1,
                          fill=WAVE["idle"], width=2, capstyle="round")
            for i in range(BARS)
        ]

        row = H - FOOTER / 2
        self.dot = c.create_oval(22, row - 4, 30, row + 4, fill=DOT["idle"], outline="")
        self.title = c.create_text(40, row, text="Диктовка", anchor="w", fill=TEXT,
                                   font=("Segoe UI", 9))
        # чип с клавишей прячется, когда слева идёт длинная подпись состояния
        chip(c, 104, row, self.hotkey, tags="modechip")

        c.create_text(W - 190, row, text="Стоп", anchor="w", fill=DIM, font=("Segoe UI", 9),
                      tags="stop")
        chip(c, W - 156, row, self.hotkey, tags="stop")
        c.create_text(W - 120, row, text="|", anchor="w", fill=SEP, font=("Segoe UI", 9))
        c.create_text(W - 106, row, text="Отмена", anchor="w", fill=DIM, font=("Segoe UI", 9),
                      tags="cancel")
        chip(c, W - 52, row, "Esc", tags="cancel")

        c.tag_bind("stop", "<Button-1>", lambda _e: self.rec.toggle())
        c.tag_bind("cancel", "<Button-1>", lambda _e: self.rec.cancel())

    # --- цикл обновления --------------------------------------------------

    def _target_heights(self, state: str) -> list[float]:
        if state == "busy":  # распознавание: волна замирает и слегка оседает
            return [max(1.0, h * 0.9) for h in self.heights]
        levels = list(self.rec.levels)
        if not levels:
            return [1.0] * BARS
        levels = levels[-BARS:]
        pad = [0.0] * (BARS - len(levels))
        top = (H - FOOTER) / 2 - 10
        return [min(1.0, v) * top for v in pad + levels]

    def set_hotkey(self, key: str) -> None:
        """Смена клавиши. Рисовать отсюда нельзя — зовут из потока меню, а Tk этого не терпит."""
        self._new_hotkey = key

    def ask_for_file(self) -> None:
        """Просьба из меню показать выбор файла. Само окно откроет _tick.

        Открывать диалог прямо здесь нельзя: Tk работает только в своём потоке,
        а меню лотка живёт в другом — программа падала бы через раз.
        """
        self._ask_file = True

    def _choose_files(self) -> None:
        from tkinter import filedialog

        import transcribe as tr

        masks = " ".join(f"*{suffix}" for suffix in tr.SUPPORTED)
        paths = filedialog.askopenfilenames(
            title="Выбери запись для расшифровки",
            filetypes=[("Аудиофайлы", masks), ("Все файлы", "*.*")],
        )
        if paths and self.on_files:
            self.on_files(list(paths))

    def _tick(self) -> None:
        if self._ask_file:
            self._ask_file = False
            self._choose_files()
        if self._new_hotkey:
            # чипы разной ширины под разные названия клавиш, поэтому собираем заново
            self.hotkey, self._new_hotkey = self._new_hotkey.upper(), None
            self.canvas.delete("all")
            self._build()
        if self.should_quit.is_set():
            self.root.destroy()
            return

        state = self.rec.state
        notice = self.rec.notice_text()
        want_visible = state != "idle" or notice is not None
        if want_visible != self.visible:
            self.visible = want_visible
            if want_visible:
                self.root.geometry(self._geometry())
                self.root.deiconify()
                self.root.attributes("-topmost", True)
            else:
                self.root.withdraw()

        if self.visible:
            c = self.canvas
            targets = self._target_heights(state)
            mid = (H - FOOTER) / 2 + 4
            color = WAVE[state]
            for i, bar in enumerate(self.bars):
                self.heights[i] += (targets[i] - self.heights[i]) * 0.5
                h = max(1.0, self.heights[i])
                x = WAVE_PAD + i * WAVE_STEP
                c.coords(bar, x, mid - h, x, mid + h)
                c.itemconfig(bar, fill=color)
            c.itemconfig(self.dot, fill=DOT[state])
            if notice:
                c.itemconfig(self.title, text=notice[:34], fill="#ff9f0a")
            elif state == "busy":
                c.itemconfig(self.title, text="распознаю…", fill=TEXT)
            else:
                c.itemconfig(self.title, text="Диктовка", fill=TEXT)
            plain = not notice and state != "busy"
            c.itemconfig("modechip", state="normal" if plain else "hidden")

        self.root.after(50, self._tick)

    def run(self) -> None:
        self.root.mainloop()


def check_corners(win) -> None:
    """Смотрит на угловой пиксель живого окна: тела капсулы там быть не должно.

    Проверять на глаз нельзя, и это выяснилось дорого. Квадратные углы видны
    только на светлом фоне, а разработчик смотрит в тёмное окно — поломка
    прожила три версии. Хуже того, первая починка «прошла» ложно: она
    спрашивала Windows, задана ли область окна, Windows отвечала «да», а
    рисовать по ней всё равно не рисовала. Отчёт был, действия не было.
    Поэтому спрашивать надо не о намерении, а о том, что на экране.
    """
    from PIL import ImageGrab  # нужен только для самопроверки, в программу не входит

    win.root.update()
    x, y = win.root.winfo_x(), win.root.winfo_y()
    shot = ImageGrab.grab((x, y, x + W, y + H), all_screens=True)
    corner, body = shot.getpixel((2, 2)), shot.getpixel((W // 2, 20))
    assert corner != body, f"угол одного цвета с телом капсулы {body} — скругления нет"
    print(f"углы: скругление {'системное' if win.system_rounded else 'своё, по цвету-ключу'}, "
          f"стекло: {'да' if win.glass else 'нет'}; угол {corner}, тело {body}")


def demo() -> None:
    """Самопроверка без микрофона: прогоняет окно по состояниям."""
    import math

    class FakeRecorder:
        def __init__(self):
            self.levels = []
            self.state = "recording"
            self._notice = None
            self.t = 0

        def notice_text(self):
            return self._notice

        def toggle(self):
            self.state = "busy" if self.state == "recording" else "recording"

        def cancel(self):
            self.state = "idle"

    rec = FakeRecorder()
    win = VoiceWindow(rec, "f8")
    win.visible = True
    win.root.deiconify()
    check_corners(win)

    def feed():
        rec.t += 1
        rec.levels.append(abs(math.sin(rec.t / 6)) * (0.3 + 0.7 * abs(math.sin(rec.t / 31))))
        del rec.levels[:-BARS]
        if rec.t == 100:
            rec.state = "busy"
        if rec.t == 160:
            rec.state, rec._notice = "idle", "речь не распознана"
        if rec.t == 220:
            win.should_quit.set()
        win.root.after(50, feed)

    win.root.after(50, feed)
    win.run()
    print("demo: окно отработало все состояния и закрылось")


if __name__ == "__main__":
    demo()
