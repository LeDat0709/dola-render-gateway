#!/usr/bin/env python3
"""Regression tests for cookie_service.parse_cookie_input.

Guards the bug where a normal "k=v; k=v; ..." string with >=7 pairs was mis-detected
as Netscape format and mangled, silently dropping sessionid.

    python test_cookie_parse.py
"""
from cookie_service import parse_cookie_input


def _sid(cookies):
    return next((c for c in cookies if c["name"] == "sessionid"), None)


def test_kv_many_pairs_keeps_sessionid():
    kv = "i18next=ja; sessionid=abc.def.ghi; csrftoken=tok123; other=1; a=2; b=3; c=4"
    out = parse_cookie_input(kv)
    assert len(out) == 7, out
    sid = _sid(out)
    assert sid and sid["value"] == "abc.def.ghi" and sid["domain"] == ".dola.com"


def test_real_netscape_still_parses():
    line = "\t".join([".dola.com", "TRUE", "/", "TRUE", "1799999999", "sessionid", "xyz.val"])
    out = parse_cookie_input(line)
    sid = _sid(out)
    assert sid and sid["value"] == "xyz.val"


def test_pipe_format_extracts_cookie_part():
    pipe = "6159|pass||sessionid=s1; csrftoken=t1; i18next=ja|||Mozilla/5.0"
    out = parse_cookie_input(pipe)
    assert _sid(out) and _sid(out)["value"] == "s1"


def test_json_object_of_name_value_pairs():
    """File xuất của tool khác lưu cookies là {tên: giá_trị} (không có khoá name/value)."""
    obj = '{"sessionid": "98528afc", "i18next": "vi", "ttwid": "1%7COoIR%7C1788", "flow_x": "Kz9b=="}'
    out = parse_cookie_input(obj)
    assert [c["name"] for c in out] == ["sessionid", "i18next", "ttwid", "flow_x"], out
    sid = _sid(out)
    assert sid["value"] == "98528afc" and sid["domain"] == ".dola.com" and sid["httpOnly"]
    assert next(c for c in out if c["name"] == "flow_x")["value"] == "Kz9b=="


def test_json_list_of_cookie_dicts_unchanged():
    out = parse_cookie_input('[{"name": "sessionid", "value": "v1", "domain": "www.dola.com"}]')
    assert _sid(out) and _sid(out)["domain"] == "www.dola.com"


if __name__ == "__main__":
    test_kv_many_pairs_keeps_sessionid()
    test_real_netscape_still_parses()
    test_pipe_format_extracts_cookie_part()
    test_json_object_of_name_value_pairs()
    test_json_list_of_cookie_dicts_unchanged()
    print("cookie parse tests OK")
