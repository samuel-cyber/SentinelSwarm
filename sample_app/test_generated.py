import pytest
from app import register_user

def test_register_user_age_none():
    result, err = register_user({"name": "Test", "email": "test@example.com"})
    assert result is None
    assert err is not None
