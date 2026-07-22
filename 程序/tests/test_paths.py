"""原子写出与临时残留清理（防"中途失败留下半截损坏产物 / 垃圾文件"）。"""
from pathlib import Path

import pytest

from src.paths import OutputError, atomic_output, sweep_temp


def test_atomic_output_success_replaces_and_leaves_no_part(tmp_path):
    final = tmp_path / "out.txt"
    with atomic_output(str(final)) as h:
        assert h.tmp.endswith(".part")
        Path(h.tmp).write_text("done", encoding="utf-8")
        assert not final.exists(), "落位前最终文件不该出现"
    assert final.read_text(encoding="utf-8") == "done"
    assert h.path == str(final)
    assert not Path(h.tmp).exists(), "成功后不留 .part"


def test_atomic_output_failure_leaves_nothing(tmp_path):
    final = tmp_path / "out.txt"
    with pytest.raises(RuntimeError, match="boom"):
        with atomic_output(str(final)) as h:
            Path(h.tmp).write_text("partial", encoding="utf-8")
            raise RuntimeError("boom")
    assert not final.exists(), "失败绝不能在最终路径留下半截文件"
    assert not (tmp_path / "out.txt.part").exists(), "失败要清掉 .part"


def test_atomic_output_does_not_clobber_existing_on_failure(tmp_path):
    """已有一份好文件时，新一次生成失败不得破坏它。"""
    final = tmp_path / "out.txt"
    final.write_text("GOOD", encoding="utf-8")
    with pytest.raises(RuntimeError):
        with atomic_output(str(final)) as h:
            Path(h.tmp).write_text("half", encoding="utf-8")
            raise RuntimeError("x")
    assert final.read_text(encoding="utf-8") == "GOOD", "旧的好文件应原样保留"


def test_atomic_output_locked_target_falls_back_to_free_name(tmp_path):
    """最终名无法覆盖（这里用"已是目录"模拟被占用）→ 换个名保住成果。"""
    final = tmp_path / "out.pdf"
    final.mkdir()                       # 占住最终名，os.replace 会失败
    with atomic_output(str(final)) as h:
        Path(h.tmp).write_text("data", encoding="utf-8")
    assert h.path != str(final), "应改用不冲突的名字"
    assert Path(h.path).read_text(encoding="utf-8") == "data"
    assert not Path(h.tmp).exists()


def test_sweep_temp_removes_only_our_siblings(tmp_path):
    base = tmp_path / "translations.json"
    base.write_text("{}", encoding="utf-8")
    (tmp_path / "translations.json.tmp").write_text("x", encoding="utf-8")
    (tmp_path / "translations.json.part").write_text("y", encoding="utf-8")
    keep = tmp_path / "user.txt"
    keep.write_text("keep", encoding="utf-8")

    sweep_temp(str(base))
    assert not (tmp_path / "translations.json.tmp").exists()
    assert not (tmp_path / "translations.json.part").exists()
    assert base.exists() and keep.exists(), "只删自己的 .tmp/.part，别的不动"
