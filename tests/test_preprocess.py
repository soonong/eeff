from __future__ import annotations

from pathlib import Path

from app.preprocess import html_to_markdown


def test_strips_script_style_nav():
    html = "<html><head><script>alert(1)</script><style>x{}</style></head><body><nav>x</nav><p>본문</p></body></html>"
    out = html_to_markdown(html)
    assert "alert" not in out
    assert "x{}" not in out
    assert "본문" in out


def test_table_to_markdown():
    html = """
    <table>
      <tr><th>항목</th><th>금액</th></tr>
      <tr><td>기초금액</td><td>3,922,300,000</td></tr>
    </table>
    """
    out = html_to_markdown(html)
    assert "| 항목 | 금액 |" in out
    assert "| --- | --- |" in out
    assert "| 기초금액 | 3,922,300,000 |" in out


def test_table_with_colspan_expands():
    html = "<table><tr><td colspan='2'>제목</td></tr><tr><td>A</td><td>B</td></tr></table>"
    out = html_to_markdown(html)
    assert "| 제목 | 제목 |" in out
    assert "| A | B |" in out


def test_table_with_rowspan_carries():
    html = "<table><tr><td rowspan='2'>L</td><td>A</td></tr><tr><td>B</td></tr></table>"
    out = html_to_markdown(html)
    assert "| L | A |" in out
    assert "| L | B |" in out


def test_pipe_in_cell_is_escaped():
    html = "<table><tr><td>a|b</td><td>c</td></tr></table>"
    out = html_to_markdown(html)
    assert "a\\|b" in out


def test_collapses_blank_lines():
    html = "<p>a</p><p></p><p></p><p>b</p>"
    out = html_to_markdown(html)
    assert "\n\n\n" not in out


def test_sample_fixture_renders():
    html = (Path(__file__).resolve().parent.parent / "samples" / "sample_g2b.html").read_text(encoding="utf-8")
    out = html_to_markdown(html)
    assert "기초금액" in out
    assert "포장공사업" in out
    assert "alert" not in out
