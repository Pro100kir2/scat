"""Optional contact phone of whoever installs a probe: admin-only, stored as E.164.

Accepts what people actually type: spaces, dashes, dots, brackets, a leading 00, and the
Russian trunk prefix 8 (8 912 345-67-89 → +79123456789). Anything else is refused rather
than guessed, so the admin never sees a number that is silently wrong.
"""
import re
from typing import Optional

_SEPARATORS_RE = re.compile(r"[\s\-.() ]")
E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")


class InvalidPhone(ValueError):
    pass


def normalize_phone(raw: Optional[str]) -> Optional[str]:
    """E.164 string, None for empty input, InvalidPhone otherwise."""
    if raw is None:
        return None
    text = _SEPARATORS_RE.sub("", str(raw))
    if not text:
        return None
    if text.startswith("00"):
        text = "+" + text[2:]
    elif not text.startswith("+"):
        if len(text) == 11 and text[0] in "78":  # Russia/Kazakhstan: 8XXXXXXXXXX or 7XXXXXXXXXX
            text = "+7" + text[1:]
        elif len(text) == 10 and text[0] == "9":  # a Russian mobile number without any prefix
            text = "+7" + text
        else:
            raise InvalidPhone("enter the number in international format, e.g. +7 912 345-67-89")
    if not E164_RE.match(text):
        raise InvalidPhone("enter the number in international format, e.g. +7 912 345-67-89")
    return text


def mask_phone(phone: Optional[str]) -> Optional[str]:
    """For places where only a hint is needed: +7912•••••89."""
    if not phone:
        return None
    return phone[:5] + "•" * max(len(phone) - 7, 0) + phone[-2:]
