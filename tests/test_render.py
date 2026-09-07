"""Tests for dnsctl_lib/render.py: the table/CSV/Markdown cell renderers
used by `dnsctl.py show`."""
from __future__ import annotations


def test_render_table_aligns_columns_to_widest_cell(mod):
    # Column 0's widest cell is "333" (width 3), column 1's is "BB" (width 2) -
    # widths must come from whichever of header/rows is longest per column.
    headers = ["A", "BB"]
    rows = [["1", "22"], ["333", "4"]]

    table = mod.render.render_table(headers, rows)
    lines = table.splitlines()

    assert lines[0] == "A    BB"
    assert lines[1] == "---  --"
    assert lines[2] == "1    22"
    assert lines[3] == "333  4 "


def test_render_table_handles_no_rows(mod):
    table = mod.render.render_table(["A", "B"], [])
    assert table.splitlines() == ["A  B", "-  -"]


def test_render_csv_matches_expected_output(mod):
    csv_text = mod.render.render_csv(["Zone", "Type"], [["example.com", "A"], ["example.org", "TXT"]])
    assert csv_text == "Zone,Type\nexample.com,A\nexample.org,TXT\n"


def test_render_csv_quotes_cells_containing_commas(mod):
    csv_text = mod.render.render_csv(["Value"], [["a,b"]])
    assert csv_text == 'Value\n"a,b"\n'


def test_render_markdown_escapes_pipe_characters(mod):
    md = mod.render.render_markdown(["Name", "Value"], [["caa", "0 issue \"letsencrypt.org|pki.goog\""]])
    lines = md.splitlines()
    assert lines[0] == "| Name | Value |"
    assert lines[1] == "| --- | --- |"
    assert lines[2] == '| caa | 0 issue "letsencrypt.org\\|pki.goog" |'


def test_render_markdown_handles_empty_cell(mod):
    md = mod.render.render_markdown(["A", "B"], [["", "x"]])
    assert md.splitlines()[2] == "|  | x |"
