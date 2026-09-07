def test_find_zone_block_finds_correct_bounds(mod, config_from):
    config_from("multi_zone.js")
    lines = mod.read_dnsconfig_lines()
    start, end = mod.find_zone_block(lines, "example.com")
    assert lines[start].startswith('D("example.com"')
    assert lines[end].strip() == ");"

    start2, _end2 = mod.find_zone_block(lines, "example.org")
    assert lines[start2].startswith('D("example.org"')
    assert start2 > end  # example.org's block comes after example.com's block


def test_insert_record_line_lands_in_correct_zone_only(mod, config_from):
    config_from("multi_zone.js")
    lines = mod.read_dnsconfig_lines()
    new_line = mod.build_record_line("A", "newhost", "5.6.7.8")
    mod.insert_record_line(lines, new_line, "example.com")

    # insert_record_line mutates `lines` in place (one parse threaded through,
    # not re-read from disk) - it already reflects the new state.
    start_m, end_m = mod.find_zone_block(lines, "example.com")
    start_h, end_h = mod.find_zone_block(lines, "example.org")

    example_com_block = "\n".join(lines[start_m : end_m + 1])
    example_org_block = "\n".join(lines[start_h : end_h + 1])

    assert "newhost" in example_com_block
    assert "newhost" not in example_org_block

    # And it was actually written to disk, not just mutated in memory.
    on_disk = mod.DNSCONFIG_FILE.read_text(encoding="utf-8").splitlines()
    assert on_disk == lines


def test_insert_then_find_record_lines(mod, config_from):
    config_from("multi_zone.js")
    lines = mod.read_dnsconfig_lines()
    new_line = mod.build_record_line("A", "newhost", "5.6.7.8")
    mod.insert_record_line(lines, new_line, "example.com")

    matches = mod.find_record_lines(lines, "newhost", "example.com")
    assert len(matches) == 1
    assert "5.6.7.8" in matches[0][1]

    # Must not appear as a match in the other zone.
    assert mod.find_record_lines(lines, "newhost", "example.org") == []


def test_remove_line_at_keeps_other_zone_intact(mod, config_from):
    config_from("multi_zone.js")
    lines = mod.read_dnsconfig_lines()
    matches = mod.find_record_lines(lines, "www", "example.com")
    assert len(matches) == 1
    idx, _line = matches[0]

    mod.remove_line_at(lines, idx)

    assert mod.find_record_lines(lines, "www", "example.com") == []
    # example.org's "www" CNAME must still be there - line indices shifted by
    # exactly one removal above it, and `lines` was mutated in place to match.
    assert len(mod.find_record_lines(lines, "www", "example.org")) == 1


def test_replace_line_at_does_not_shift_other_indices(mod, config_from):
    config_from("multi_zone.js")
    lines = mod.read_dnsconfig_lines()
    matches = mod.find_record_lines(lines, "www", "example.com")
    idx, _old_line = matches[0]

    new_line = mod.build_record_line("CNAME", "www", "changed.example.com.")
    mod.replace_line_at(lines, idx, new_line)

    assert "changed.example.com." in lines[idx]

    # example.org's zone block should be completely unaffected (same line
    # count, same content) since replace never shifts indices.
    start_h, end_h = mod.find_zone_block(lines, "example.org")
    assert any("example.org." in lines[i] for i in range(start_h, end_h + 1))


def test_insert_multiple_records_preserves_multi_zone_isolation(mod, config_from):
    config_from("multi_zone.js")
    lines = mod.read_dnsconfig_lines()
    mod.insert_record_line(lines, mod.build_record_line("A", "one", "1.1.1.1"), "example.com")
    mod.insert_record_line(lines, mod.build_record_line("A", "two", "2.2.2.2"), "example.org")
    mod.insert_record_line(lines, mod.build_record_line("A", "three", "3.3.3.3"), "example.com")

    assert len(mod.find_record_lines(lines, "one", "example.com")) == 1
    assert len(mod.find_record_lines(lines, "three", "example.com")) == 1
    assert len(mod.find_record_lines(lines, "two", "example.org")) == 1

    assert mod.find_record_lines(lines, "one", "example.org") == []
    assert mod.find_record_lines(lines, "two", "example.com") == []


def test_read_dnsconfig_lines_reflects_current_disk_state(mod, config_from):
    """Sanity check for the helper itself: it's a plain read, so a second
    call after an on-disk change sees the new content (callers that need a
    stable view within one command must reuse the first result)."""
    config_from("multi_zone.js")
    before = mod.read_dnsconfig_lines()
    mod.DNSCONFIG_FILE.write_text(
        mod.DNSCONFIG_FILE.read_text(encoding="utf-8") + "// marker\n", encoding="utf-8"
    )
    after = mod.read_dnsconfig_lines()
    assert after != before
    assert after[-1] == "// marker"
