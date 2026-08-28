"""Настройки диктовки: правка одной строки не должна портить остальной файл."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import voice_settings as vs  # noqa: E402


def test_reads_values_ignoring_comments(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# комментарий\nVOICE_HOTKEY=f8\n\nASR_MODEL=\"gigaam-v3-e2e-rnnt\"\n", encoding="utf-8")
    assert vs.read_all(env) == {"VOICE_HOTKEY": "f8", "ASR_MODEL": "gigaam-v3-e2e-rnnt"}


def test_missing_file_is_not_an_error(tmp_path):
    assert vs.read_all(tmp_path / "нет.env") == {}


def test_write_keeps_comments_and_order(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# как выбирать клавишу\nVOICE_HOTKEY=f8\nVOICE_GAIN=2.2\n", encoding="utf-8")

    vs.write(env, "VOICE_HOTKEY", "f9")

    assert env.read_text(encoding="utf-8") == "# как выбирать клавишу\nVOICE_HOTKEY=f9\nVOICE_GAIN=2.2\n"


def test_write_appends_unknown_key(tmp_path):
    env = tmp_path / ".env"
    env.write_text("VOICE_GAIN=2.2\n", encoding="utf-8")
    vs.write(env, "ASR_MODEL", "gigaam-multilingual-ctc")
    assert vs.read_all(env)["ASR_MODEL"] == "gigaam-multilingual-ctc"
    assert vs.read_all(env)["VOICE_GAIN"] == "2.2"


def test_write_creates_file(tmp_path):
    env = tmp_path / ".env"
    vs.write(env, "VOICE_HOTKEY", "f8")
    assert vs.read_all(env) == {"VOICE_HOTKEY": "f8"}


@pytest.mark.parametrize(
    "text,expected",
    [("1", True), ("да", True), ("0", False), ("нет", False), ("", True), (None, True)],
)
def test_bool_understands_human_answers(text, expected):
    assert vs.as_bool(text, default=True) is expected
