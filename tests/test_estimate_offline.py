"""条款6 · scripts/cost/estimate_offline.py 的 --book 校验回归。

钉住的是一个真实缺陷：原先传未登记的 --book 时，主循环里
`if args.book and key != args.book: continue` 会静默跳过所有书——退出码 0、
除标定行外没有任何输出。调用方（包括 run_novel.py 打印的提示命令）无从得知
估算根本没跑。实测：`--book jianke` 返回 0 且空输出。

这些用例只验 --book 的校验分支与退出码，把 load_points/calibrate/detect_and_read
等重量级 IO 全部打桩，因此不读真实语料、不调 API、不写库。
"""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = ROOT / "scripts" / "cost" / "estimate_offline.py"


@pytest.fixture(scope="module")
def est():
    """Import the estimator by path — scripts/ is not a package."""
    spec = importlib.util.spec_from_file_location("estimate_offline", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["estimate_offline"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def stub_io(est, monkeypatch):
    """把标定与语料读取打桩，只留 --book 校验逻辑真实运行。

    注意 load_points/calibrate/md_chapter_lengths/predict_chapter 是
    estimate_offline 自己的函数，打在 est 上；但 detect_and_read/split_chapters
    是 main() 里 `from src.text_io import ...` 的函数级导入，必须打在**真实源
    模块**上，打在 est 上不生效。

    桩语料给一章非空内容：0 章会让估算末尾的 `每千词 ¥cost/(words/1000)` 除零
    （既有边界，非本回归目标），把注意力从 --book 校验上带偏。
    """
    monkeypatch.setattr(est, "load_points", lambda: [])
    monkeypatch.setattr(est, "calibrate", lambda pts: {})
    monkeypatch.setattr(est, "BOOKS", {
        "difu": {"name": "地府", "src": "x.txt", "out": "y.md", "genre": "difu"},
    })
    monkeypatch.setattr(est, "md_chapter_lengths", lambda p: {1: 200})
    monkeypatch.setattr(est, "predict_chapter",
                        lambda cal, genre, src, en: {
                            "read": (100.0, 50.0),
                            "write": (100.0, 50.0),
                            "readback": (100.0, 50.0),
                        })
    monkeypatch.setattr("src.text_io.detect_and_read", lambda p: ("", "utf-8"))
    # action=None != ParagraphTag.SKIP → 这章不会被过滤掉。
    monkeypatch.setattr("src.chapter_splitter.split_chapters",
                        lambda t: [SimpleNamespace(index=1, content="abc", action=None)])


def test_unknown_book_hard_fails_with_exit_code_2(est, stub_io, capsys):
    """未登记书目 → 返回 2（硬失败），不再静默返回 0。"""
    rc = est.main(["--book", "jianke"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "未知书目 'jianke'" in err
    # 必须告诉用户哪些 key 可用，否则报错也没法行动。
    assert "difu" in err


def test_unknown_book_message_warns_key_names_differ(est, stub_io, capsys):
    """报错要点明本脚本的 key 与 run_novel.py 的 NOVELS key 不一致。"""
    est.main(["--book", "jianke"])
    err = capsys.readouterr().err
    assert "run_novel.py" in err
    assert "NOVELS" in err


def test_known_book_passes_validation(est, stub_io, capsys):
    """已登记书目 → 通过校验，正常跑完返回 None（退出码 0）。"""
    rc = est.main(["--book", "difu"])
    assert rc is None
    # 校验通过后会打印标定行，证明没有被 --book 分支挡下。
    assert "标定" in capsys.readouterr().out


def test_no_book_arg_passes_validation(est, stub_io):
    """不带 --book（估全部）→ 不触发未知书目校验，正常返回 None。"""
    assert est.main([]) is None
