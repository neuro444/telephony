"""PII helpers for the telephony gateway.

Keeps personally-identifiable data out of flat on-disk logs (JSONL cost and
order files) while leaving the full values available in live call state and
in chat_manager's auth-gated database, where authorised staff can still reach
them through the dashboard.

Rules
-----
- mask_phone() is used *only* at the disk-write edge in cost_emitter.py and
  orders/emitter.py.  Everything else (routing, session lookup, CRM) keeps the
  real number.
- When MASK_PII_LOGS is False the function is a no-op so local dev and the
  automated test suite can assert on exact phone-number values without running
  a separate decode step.
- The masked form (+1404***1333) keeps the country code and last four digits so
  it is recognisable during debugging without exposing the full subscriber number.
"""
import config


def mask_phone(phone: str) -> str:
    """Return a redacted form of an E.164 phone number for log storage.

    Keeps the leading country-code prefix (up to 5 chars) and the last 4
    digits so different test numbers are still distinguishable at a glance:

        +14042071333  ->  +1404***1333
        +919876543210 ->  +9198***3210
        +1            ->  ***            (too short to mask meaningfully)

    When config.MASK_PII_LOGS is False the original value is returned unchanged
    so unit tests can compare exact strings without extra decoding.
    """
    if not config.MASK_PII_LOGS:
        return phone
    if not phone or len(phone) < 8:
        return "***"
    return f"{phone[:5]}***{phone[-4:]}"
