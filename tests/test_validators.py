import pytest

from utils.validators import is_valid_email, normalize_phone


@pytest.mark.parametrize("email", [
    "info@miamistronggym.com",
    "contact.us+sales@sub.domain.net",
    "OWNER@GYM.IO",
])
def test_valid_emails(email):
    assert is_valid_email(email) is True


@pytest.mark.parametrize("email", [
    "",
    "bademail",
    "user@nodot",
    "user@site.c",
    "noreply@gym.com",
    "no-reply@gym.com",
    "donotreply@gym.com",
    "mailer@gym.com",
    "daemon@gym.com",
    "user@example.com",
    "user@test.com",
    "user@domain.com",
    "a" * 250 + "@x.com",
])
def test_invalid_emails(email):
    assert is_valid_email(email) is False


@pytest.mark.parametrize("email", [
    "abc123@sentry-next.wixpress.com",   # Sentry DSN false positive
    "hash@o123.ingest.sentry.io",        # subdomain of sentry.io
    "deadbeef@wixpress.com",
    "x@vercel.app",
    "y@netlify.app",
    "z@githubusercontent.github.io",
    "noone@placeholder.com",
    "test@johndoe.com",
])
def test_technical_domains_rejected(email):
    # Deuda 1: infra/placeholder domains must never pass as contact emails.
    assert is_valid_email(email) is False


@pytest.mark.parametrize("phone,country,expected", [
    ("(305) 504-6980", "US", "+13055046980"),
    ("305-504-6980", "US", "+13055046980"),
    ("+1 305 504 6980", "US", "+13055046980"),
    ("02323-516505", "AR", "+542323516505"),
    ("+54 9 11 5555-1234", "US", "+5491155551234"),
])
def test_normalize_phone_formats(phone, country, expected):
    assert normalize_phone(phone, country) == expected


@pytest.mark.parametrize("phone", ["", "   ", "12345", "not a phone"])
def test_normalize_phone_invalid(phone):
    assert normalize_phone(phone) == ""
