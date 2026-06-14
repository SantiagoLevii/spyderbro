import logging
import re

import phonenumbers

logger = logging.getLogger(__name__)

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")

BLOCKED_LOCAL_PARTS = ("noreply", "no-reply", "donotreply", "mailer", "daemon")
BLOCKED_DOMAINS = ("example.com", "test.com", "domain.com")

# Technical / infrastructure / placeholder domains that are never real contact
# emails (Sentry DSNs, CDNs, PaaS hosts, demo addresses). Matched as the exact
# domain or any subdomain of it.
TECHNICAL_DOMAINS_BLACKLIST = (
    "sentry.io", "sentry-next.wixpress.com", "wixpress.com",
    "amazonaws.com", "cloudfront.net", "fastly.net",
    "akamai.net", "cloudflare.com", "heroku.com",
    "netlify.app", "vercel.app", "github.io",
    "googleapis.com", "googletagmanager.com",
    "example.com", "test.com", "domain.com", "email.com",
    "johndoe.com", "placeholder.com",
)

MAX_EMAIL_LENGTH = 254


def is_valid_email(email: str) -> bool:
    """Validate an email address by format and rules, without external requests.

    Args:
        email: Candidate email address.

    Returns:
        True if the email has a valid format, a real-looking domain, and is
        not a generic trap or example address.
    """
    if not email:
        return False

    email = email.strip().lower()

    if len(email) >= MAX_EMAIL_LENGTH:
        return False

    if not EMAIL_REGEX.match(email):
        return False

    local_part, _, domain = email.rpartition("@")

    if "." not in domain:
        return False

    if local_part in BLOCKED_LOCAL_PARTS:
        return False

    if domain in BLOCKED_DOMAINS:
        return False

    # Reject technical/infrastructure/placeholder domains (and their subdomains).
    for blocked in TECHNICAL_DOMAINS_BLACKLIST:
        if domain == blocked or domain.endswith("." + blocked):
            return False

    tld = domain.rsplit(".", 1)[-1]
    if len(tld) <= 1:
        return False

    return True


def normalize_phone(phone: str, default_country: str = "US") -> str:
    """Normalize a phone number to E.164 international format.

    Examples:
        "(305) 504-6980"  -> "+13055046980"
        "02323-516505"    -> "+542323516505" (with default_country="AR")
        "+1 305 504 6980" -> "+13055046980"

    Args:
        phone: Raw phone number in any common format.
        default_country: ISO country code used when the number has no
            international prefix.

    Returns:
        The number in E.164 format, or an empty string if it cannot be
        parsed or is not a valid number.
    """
    if not phone or not phone.strip():
        return ""

    region = None if phone.strip().startswith("+") else default_country

    try:
        parsed = phonenumbers.parse(phone, region)
    except phonenumbers.NumberParseException as exc:
        logger.debug("Could not parse phone %r: %s", phone, exc)
        return ""

    if not phonenumbers.is_valid_number(parsed):
        logger.debug("Phone %r parsed but is not a valid number", phone)
        return ""

    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
