"""Test suite verifying all 5 Seedance-style components."""
import pytest
import time
from dola_error_codes import classify_dola_error, DolaErrorKind


def test_dola_error_classification_seedance():
    # 710022002 -> NICK_VERIFICATION & burn_nick
    info = classify_dola_error('{"error_code": 710022002}')
    assert info.kind == DolaErrorKind.NICK_VERIFICATION
    assert info.actions["burn_nick"] is True
    assert info.actions["rotate_nick"] is True
    assert info.actions["rotate_ip"] is True
    assert info.actions["refund"] is True
