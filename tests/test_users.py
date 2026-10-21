"""The list of invited people: codes, how they are checked, and what a renewed or revoked code does."""
import json
import os
import stat

import pytest

from app import users as us


@pytest.fixture
def reg(tmp_path):
    return us.Registry(tmp_path / "users.json")


def test_a_code_is_long_readable_and_typed_forgivingly():
    c = us.new_code()
    assert len(c) == 19 and c.count("-") == 3 and all(ch in us.ALPHABET + "-" for ch in c)
    assert us.normalise_code(c.lower()) == c and us.normalise_code(c.replace("-", " ")) == c and us.normalise_code(f"  {c}  ") == c
    assert us.normalise_code("a calm river at dawn") is None and us.normalise_code("ABCD-EFGH") is None
    assert us.normalise_code("0000-0000-0000-0000") is None  # a zero is not in the alphabet
    assert len({us.new_code() for _ in range(200)}) == 200


def test_the_code_is_shown_once_and_only_its_hash_is_kept(reg, tmp_path):
    user, code = reg.create("  Amina   Diallo ", note="cousin")
    assert user["name"] == "Amina Diallo" and "code_hash" not in user and user["lookup"] == code[:4]
    raw = (tmp_path / "users.json").read_text()
    assert code not in raw and code.replace("-", "") not in raw and "scrypt$" in raw
    assert stat.S_IMODE(os.stat(tmp_path / "users.json").st_mode) == 0o600
    assert all("code_hash" not in u for u in reg.list()) and "code_hash" not in reg.get(user["id"])


def test_the_right_code_logs_in_and_nothing_else_does(reg):
    amina, code = reg.create("Amina")
    bob, code_b = reg.create("Bob")
    assert reg.check_code(code)["id"] == amina["id"] and reg.check_code(code.lower())["id"] == amina["id"]
    assert reg.check_code(code_b)["id"] == bob["id"]
    wrong = code[:-1] + ("A" if code[-1] != "A" else "B")
    assert reg.check_code(wrong) is None and reg.check_code("not a code") is None and reg.check_code("") is None
    other_label = code_b[:4] + code[4:]  # Bob's label with Amina's secret part
    assert reg.check_code(other_label) is None
    assert reg.get(amina["id"])["last_login"] is not None


def test_renewing_or_blocking_logs_the_person_out_and_stops_the_old_code(reg):
    user, code = reg.create("Amina")
    v = reg.version(user["id"])
    new = reg.renew_code(user["id"])
    assert new != code and reg.check_code(code) is None and reg.check_code(new)["id"] == user["id"] and reg.version(user["id"]) == v + 1
    reg.set_disabled(user["id"], True)
    assert reg.check_code(new) is None and reg.version(user["id"]) is None  # a blocked person has no valid cookie either
    reg.set_disabled(user["id"], False)
    assert reg.check_code(new)["id"] == user["id"]


def test_removing_a_person_and_the_admin_version(reg):
    user, code = reg.create("Amina")
    assert reg.any_users() is True
    reg.delete(user["id"])
    assert reg.check_code(code) is None and reg.get(user["id"]) is None and reg.version(user["id"]) is None and not reg.any_users()
    v = reg.version(us.ADMIN)
    reg.bump_admin()
    assert reg.version(us.ADMIN) == v + 1
    with pytest.raises(KeyError):
        reg.renew_code("nobody")


def test_a_name_is_required_and_only_some_fields_can_be_updated(reg):
    with pytest.raises(ValueError):
        reg.create("   ")
    user, _ = reg.create("Amina")
    out = reg.update(user["id"], native_language="  Ewe ", lookup="HACK", code_hash="x", consent_at="2026-10-22")
    assert out["native_language"] == "Ewe" and out["lookup"] == user["lookup"] and out["consent_at"] == "2026-10-22"
    assert "HACK" not in json.dumps(reg.list())
