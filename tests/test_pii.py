from app.pii import scrub_text


def test_scrub_email() -> None:
    out = scrub_text("Email me at student@vinuni.edu.vn")
    assert "student@" not in out
    assert "REDACTED_EMAIL" in out


def test_scrub_common_vietnamese_phone_formats() -> None:
    phone_numbers = (
        "0901234567",
        "090 123 4567",
        "090.123.4567",
        "090-123-4567",
        "+84 90 123 4567",
    )

    for phone_number in phone_numbers:
        out = scrub_text(f"Contact: {phone_number}")
        assert phone_number not in out
        assert "REDACTED_PHONE_VN" in out


def test_scrub_cccd() -> None:
    out = scrub_text("CCCD của tôi là 001203004567")
    assert "001203004567" not in out
    assert "REDACTED_CCCD" in out


def test_scrub_credit_card_formats() -> None:
    for card in ("4111 1111 1111 1111", "4111-1111-1111-1111", "4111111111111111", "3782 822463 10005"):
        out = scrub_text(f"card {card} please")
        assert card not in out
        assert "REDACTED_CREDIT_CARD" in out


def test_scrub_extra_phone_prefixes() -> None:
    for phone in ("84987654321", "(+84) 987 654 321", "0987.654.321"):
        out = scrub_text(f"call {phone}")
        assert phone not in out
        assert "REDACTED_PHONE_VN" in out


def test_scrub_passport() -> None:
    out = scrub_text("Passport B1234567 expires soon")
    assert "B1234567" not in out
    assert "REDACTED_PASSPORT" in out


def test_non_pii_is_untouched() -> None:
    text = "latency 150ms, request req-1a2b3c4d, model claude-sonnet-4-5, 2026-09-29"
    assert scrub_text(text) == text


def test_scrub_value_is_recursive() -> None:
    from app.pii import scrub_value

    out = scrub_value({"a": ["x student@vinuni.edu.vn"], "b": {"c": "0901234567"}, "n": 5})
    assert out == {"a": ["x [REDACTED_EMAIL]"], "b": {"c": "[REDACTED_PHONE_VN]"}, "n": 5}
