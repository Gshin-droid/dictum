"""Экранный текст программы на всех языках, какие она знает.

Здесь только то, что видит человек: пункты меню, сообщения в капсуле, окна.
Записи в журнал остаются русскими намеренно — журнал читает не пользователь,
а тот, кому его присылают при поломке.

Ключи латиницей и по смыслу: `notice.model_ready`, а не `msg17`. Русский текст
служит и запасным вариантом: перевода нет — покажется русский, а не пустота или
имя ключа. Так недоделанный перевод портит вид, но не ломает программу.

Как добавить язык: дописать код в LANGUAGES и колонку в TEXTS. Ключа может не
хватать — это допустимо, сработает запас.
"""

# Порядок важен: в таком виде языки встают в меню.
LANGUAGES = {
    "ru": "Русский",
    "kk": "Қазақша",
}
DEFAULT = "ru"          # он же запасной, когда перевода нет
FALLBACK = "ru"

TEXTS: dict[str, dict[str, str]] = {
    # --- меню значка ---
    "menu.record": {"ru": "Начать / остановить запись"},
    "menu.transcribe": {"ru": "Расшифровать аудиофайл…"},
    "menu.model": {"ru": "Язык и модель"},
    "menu.punctuate": {"ru": "Расставлять знаки препинания"},
    "menu.copy_last": {"ru": "Скопировать последнюю диктовку"},
    "menu.save_samples": {"ru": "Сохранять записи на диск"},
    "menu.hotkey": {"ru": "Горячая клавиша: {key}"},
    "menu.interface_language": {"ru": "Язык программы"},
    "menu.dictionary": {"ru": "Словарь замен"},
    "menu.help": {"ru": "Справка"},
    "menu.about": {"ru": "О программе"},
    "menu.log": {"ru": "Показать журнал"},
    "menu.quit": {"ru": "Выход"},
    "menu.model_download": {"ru": "{label} — скачать {size}"},

    # --- запуск и смена модели ---
    "notice.first_run": {"ru": "первый запуск: качаю модель, это несколько минут…"},
    "notice.ready": {"ru": "готово, можно диктовать"},
    "notice.model_failed": {"ru": "модель не загрузилась — выбери другую в меню"},
    "notice.model_loading": {"ru": "модель ещё готовится — подожди"},
    "notice.finish_first": {"ru": "сначала закончи диктовку"},
    "notice.model_preparing": {"ru": "готовлю модель {name}, это может занять минуты…"},
    "notice.model_ready": {"ru": "модель готова"},
    "notice.model_switch_failed": {"ru": "не смог сменить модель"},
    "notice.preparing_vad": {"ru": "готовлю нарезчик по тишине…"},
    "notice.preparing_punct": {"ru": "готовлю знаки препинания…"},

    # --- запись ---
    "notice.mic_busy": {"ru": "микрофон не отвечает — жду драйвер"},
    "notice.cancelled": {"ru": "запись отменена"},
    "notice.too_short": {"ru": "слишком короткая запись"},
    "notice.no_speech": {"ru": "речь не распознана"},
    "notice.error": {"ru": "ошибка: {error}"},

    # --- расшифровка файлов ---
    "notice.reading_file": {"ru": "{counter}читаю {name}"},
    "notice.transcribing": {"ru": "{counter}расшифровываю: {percent}% из {minutes} мин"},
    "notice.file_no_speech": {"ru": "{name}: речь не распознана"},
    "notice.file_done": {"ru": "готово: {words} слов → {name}"},
    "notice.file_unreadable": {"ru": "{name}: не читается"},
    "notice.file_failed": {"ru": "{name}: ошибка расшифровки"},

    # --- буфер обмена ---
    "notice.clipboard_has_text": {"ru": "текст в буфере — поставь курсор и вставь"},
    "notice.clipboard_busy": {"ru": "буфер занят — текст в меню «Скопировать последнюю диктовку»"},
    "notice.clipboard_taken": {"ru": "буфер обмена занят другой программой"},
    "notice.last_copied": {"ru": "последняя диктовка в буфере"},
    "notice.nothing_yet": {"ru": "диктовок ещё не было"},

    # --- переключатели ---
    "notice.samples_on": {"ru": "записи сохраняются"},
    "notice.samples_off": {"ru": "записи больше не сохраняются"},
    "notice.punct_on": {"ru": "знаки препинания включены"},
    "notice.punct_off": {"ru": "знаки препинания выключены"},
    "notice.language_set": {"ru": "язык программы: {language}"},

    # --- окна ---
    "about.title": {"ru": "{app} {version} — голосовая диктовка"},
    "about.body": {
        "ru": "{key} — начать и остановить диктовку, Esc — отменить.\n"
              "Модель: {model}.\n"
              "Речь обрабатывается на этом компьютере и никуда не отправляется.\n"
              "Автор: {author}. Открытый код, лицензия MIT: {url}",
    },
    "about.short": {"ru": "{app}: {model}, клавиша {key}"},
    "error.log_open": {"ru": "Журнал лежит здесь:\n{path}\n\nОткрыть не вышло: {error}"},
}

_current = DEFAULT


def set_language(code: str) -> str:
    """Ставит язык экрана. Незнакомый код — остаёмся на прежнем, это не ошибка."""
    global _current
    if code in LANGUAGES:
        _current = code
    return _current


def language() -> str:
    return _current


def t(ключ: str, /, **kwargs) -> str:
    """Строка на текущем языке. Нет перевода — русская; нет ключа — сам ключ.

    Ключ принимается только позиционно (косая черта в подписи): иначе поле
    подстановки с тем же именем сталкивается с параметром, и `t("menu.hotkey",
    key="F8")` падает вместо того, чтобы подставить клавишу.

    Подстановка не имеет права уронить программу: сообщение с испорченным
    шаблоном лучше показать как есть, чем не показать диктовку.
    """
    варианты = TEXTS.get(ключ)
    if not варианты:
        return ключ
    текст = варианты.get(_current) or варианты.get(FALLBACK) or ключ
    if not kwargs:
        return текст
    try:
        return текст.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return текст
