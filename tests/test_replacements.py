"""Словарь замен: разбор файла, замена в тексте, перечитывание после правки."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("replacements", ROOT / "replacements.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["replacements"] = module
    spec.loader.exec_module(module)
    return module


rp = _load()


def _slovar(tmp_path, text):
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / rp.FILE_NAME
    path.write_text(text, encoding="utf-8")
    return rp.Dictionary(path)


def test_prostaya_zamena(tmp_path):
    assert _slovar(tmp_path, "гугл = Google").apply("открой гугл") == "открой Google"


def test_registr_sleva_ne_vazhen(tmp_path):
    slovar = _slovar(tmp_path, "гугл = Google")
    assert slovar.apply("Гугл сказал") == "Google сказал"


def test_zaglavnaya_sohranyaetsya(tmp_path):
    """Замена в начале фразы должна остаться с заглавной буквы."""
    assert _slovar(tmp_path, "экзе = exe").apply("Экзе лежит рядом") == "Exe лежит рядом"


def test_neskolko_slov_sleva(tmp_path):
    slovar = _slovar(tmp_path, "дзен конвейер = Дзен-конвейер")
    assert slovar.apply("наш дзен конвейер готов") == "наш Дзен-конвейер готов"


def test_dlinnoe_pravilo_pobezhdaet_korotkoe(tmp_path):
    """Иначе «дзен» сработает первым и до сочетания дело не дойдёт."""
    slovar = _slovar(tmp_path, "дзен = Дзен\nдзен конвейер = Дзен-конвейер")
    assert slovar.apply("дзен конвейер") == "Дзен-конвейер"


def test_zameny_ne_ceplyayutsya_drug_za_druga(tmp_path):
    """«а = б» и «б = в» вместе не должны превращать «а» в «в»."""
    slovar = _slovar(tmp_path, "кот = пёс\nпёс = слон")
    assert slovar.apply("кот") == "пёс"


def test_chast_slova_ne_zamenyaetsya(tmp_path):
    slovar = _slovar(tmp_path, "гугл = Google")
    assert slovar.apply("гуглить не надо") == "гуглить не надо"


def test_kommentarii_i_pustye_stroki_propuskayutsya(tmp_path):
    slovar = _slovar(tmp_path, "# это пояснение\n\n   \nгугл = Google\n")
    assert slovar.apply("гугл") == "Google"


def test_stroka_bez_ravno_ne_lomaet_razbor(tmp_path):
    slovar = _slovar(tmp_path, "мусорная строка\nгугл = Google")
    assert slovar.apply("гугл") == "Google"


def test_pustaya_pravaya_chast_udalyaet_slovo(tmp_path):
    """Слово-паразит можно вычистить, оставив правую часть пустой."""
    assert _slovar(tmp_path, "э э =").apply("ну э э дальше").replace("  ", " ") == "ну дальше"


def test_neskolko_probelov_mezhdu_slovami(tmp_path):
    slovar = _slovar(tmp_path, "дзен конвейер = Дзен-конвейер")
    assert slovar.apply("наш дзен  конвейер") == "наш Дзен-конвейер"


def test_net_fayla_tekst_ne_menyaetsya(tmp_path):
    slovar = rp.Dictionary(tmp_path / "нет-такого.txt")
    assert slovar.apply("текст как был") == "текст как был"


def test_pustoy_slovar_tekst_ne_menyaetsya(tmp_path):
    assert _slovar(tmp_path, "# только пояснения\n").apply("текст") == "текст"


def test_pravka_fayla_podhvatyvaetsya_bez_perezapuska(tmp_path):
    path = tmp_path / rp.FILE_NAME
    path.write_text("гугл = Google", encoding="utf-8")
    slovar = rp.Dictionary(path)
    assert slovar.apply("гугл") == "Google"
    # Отметка времени в наносекундах, но на всякий случай двигаем её явно:
    # два сохранения подряд могут попасть в одно значение часов файловой системы.
    path.write_text("гугл = Гугл-поиск", encoding="utf-8")
    slovar._stamp = None
    assert slovar.apply("гугл") == "Гугл-поиск"


def test_obrazec_sozdayotsya_odin_raz(tmp_path):
    path = rp.ensure_file(tmp_path)
    assert path.exists() and "Словарь замен" in path.read_text(encoding="utf-8")
    path.write_text("моё = своё", encoding="utf-8")
    rp.ensure_file(tmp_path)
    assert path.read_text(encoding="utf-8") == "моё = своё"


def test_obrazec_rabotaet_srazu(tmp_path):
    """Образец — не пример, а готовый набор: он работает с первого запуска.

    Раньше все строки были закомментированы, и человек, скачавший программу,
    получал пустой словарь. Пустой словарь не решает ту самую задачу, ради
    которой словарь и заводился: многоязычная модель пишет «гугл» кириллицей.
    """
    slovar = rp.Dictionary(rp.ensure_file(tmp_path))
    assert slovar.apply("открой гугл и эксел") == "открой Google и Excel"


def test_v_obrazce_net_pustyh_pravil():
    """Правило, где слева и справа одно и то же, не делает ничего — это опечатка."""
    # сравниваем без учёта регистра: «википедия = Википедия» тоже пустышка,
    # а разницу в одну заглавную букву программа и так наводит сама
    пустые = [левое for левое, правое in rp.parse(rp.SAMPLE)
              if левое.lower() == правое.lower()]
    assert not пустые, f"правила ничего не меняют: {пустые}"


def test_v_obrazce_net_dvusmyslennyh_slov():
    """Слова, которые чаще означают предмет, а не название, включать нельзя.

    «Зум объектива» не должен превращаться в «Zoom объектива», а «редис» — в
    «Redis». Такие строки лежат в образце закомментированными, и проверка
    сторожит именно это: список поимённый, потому что распознать двусмысленность
    в коде нечем.
    """
    опасные = {"зум", "питон", "редис", "трансформер", "смс", "халык"}
    включены = {левое.lower() for левое, _ in rp.parse(rp.SAMPLE)}
    assert not (опасные & включены), f"двусмысленные слова включены: {опасные & включены}"


def test_v_obrazce_net_protivorechivyh_dublyey():
    """Два правила на одно слово с разными ответами — тихая ловушка: сработает верхнее."""
    видел = {}
    спорные = []
    for левое, правое in rp.parse(rp.SAMPLE):
        ключ = левое.lower()
        if ключ in видел and видел[ключ] != правое:
            спорные.append((левое, видел[ключ], правое))
        видел.setdefault(ключ, правое)
    assert not спорные, f"одно слово с разными ответами: {спорные}"


# --- словарь на сотни строк ------------------------------------------------
# Одиночные слова лежат в таблице, многословные — в переборе. Разделение
# невидимо снаружи, и проверки ниже сторожат именно это: снаружи не должно
# меняться ничего, сколько бы правил в файле ни лежало.


def _mnogo_pravil(skolko: int) -> str:
    """Правила-однодневки, ни одно из которых не встретится в проверяемом тексте."""
    return "\n".join(f"пустышка{n} = Пустышка{n}" for n in range(skolko))


def test_lishnie_pravila_ne_menyayut_rezultat(tmp_path):
    """Главное свойство словаря: ответ зависит от подходящих правил, а не от их числа.

    Человек копит список годами. Если пятисотое правило способно повлиять на
    работу первого, словарь становится непредсказуемым — и заметит это только
    тот, у кого список большой, то есть очень нескоро.
    """
    текст = "открой гугл документы и телеграм, потом эксел"
    коротко = "гугл = Google\nгугл документы = Google Документы\nтелеграм = Telegram\nэксел = Excel"
    длинно = коротко + "\n" + _mnogo_pravil(500)

    было = _slovar(tmp_path / "а", коротко).apply(текст)
    стало = _slovar(tmp_path / "б", длинно).apply(текст)
    assert было == "открой Google Документы и Telegram, потом Excel"
    assert было == стало, "лишние правила изменили результат"


def test_slovo_so_znakami_sleva(tmp_path):
    """Слева не только буквы — такое правило в таблицу не ложится, но работать обязано."""
    slovar = _slovar(tmp_path, "с++ = C++\n" + _mnogo_pravil(200))
    assert slovar.apply("пишу на с++ давно") == "пишу на C++ давно"


def test_zaglavnaya_kogda_zamena_so_strochnoy(tmp_path):
    """Проверка заглавной работает, только если справа строчная буква.

    Прежняя проверка брала «Гугл = Google» — там справа и так заглавная, и
    сломанное сохранение регистра она пропускала. Поймали это случайные тексты,
    а не список примеров, поэтому случай записан сюда поимённо.
    """
    slovar = _slovar(tmp_path, "телеграмм = телеграм\n" + _mnogo_pravil(300))
    assert slovar.apply("Телеграмм молчит") == "Телеграм молчит"
    assert slovar.apply("пишу в телеграмм") == "пишу в телеграм"


def test_cepochka_ne_voznikaet_i_na_bolshom_slovare(tmp_path):
    """«а = б» и «б = в» не должны вместе превращать «а» в «в»."""
    slovar = _slovar(tmp_path, "кот = пёс\nпёс = слон\n" + _mnogo_pravil(400))
    assert slovar.apply("кот и пёс") == "пёс и слон"


def test_odno_slovo_dvazhdy_pobezhdaet_pervoe(tmp_path):
    """Два правила на одно и то же слово — работает верхнее.

    Случай не выдуманный: человек дописывает список годами и заводит дубль,
    не заметив прежней строки. Правило «верхнее главнее» он проверит сам,
    поменяв строки местами; правило «какое-нибудь из двух» проверить нельзя.

    Записано после того, как поломка `setdefault` → обычная запись в таблицу
    не уронила ни одной проверки: поведение держалось само собой.
    """
    slovar = _slovar(tmp_path, "гугл = Google\nгугл = Гугль\n" + _mnogo_pravil(100))
    assert slovar.apply("открой гугл") == "открой Google"
