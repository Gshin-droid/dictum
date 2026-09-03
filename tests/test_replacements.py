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


def test_obrazec_ne_soderzhit_deystvuyushchih_pravil(tmp_path):
    """Примеры в образце закомментированы: свежий файл не должен ничего менять."""
    slovar = rp.Dictionary(rp.ensure_file(tmp_path))
    assert slovar.apply("открой гугл и эксел") == "открой гугл и эксел"
