"""Kitchen-ticket print client.

Client only -- no print driver, no ESC/POS code, no port-9100 anything.
This module's entire job is one HTTP POST to whatever print service is
configured at PRINT_API_URL, on hangup, if there's a real order to print.

Payload shape and the /print/order call are copied verbatim from the
reference implementation:
    catering-brain/app/routers/dashboard.py:314-357 (_print_revised_order)

Preserved exactly from the reference:
- Endpoint: POST {PRINT_API_URL}/print/order
- timeout=5
- bare `except: pass` -- a dead or unreachable printer must NEVER block or
  break call hangup. If PRINT_API_URL isn't set, or the service isn't
  listening, this silently no-ops. That is correct behavior, not a bug,
  until the service is actually located and configured.
- Item shape: each item carries BOTH "quantity" and "qty" (same value,
  duplicate keys) -- kept as-is even though it looks redundant, in case
  the print service's template reads one or the other.
- "price" is the reference's field name for the item's LINE total (not
  unit price). Kept as-is to match what the print service expects.

NOT copied verbatim -- adapted, because the reference source objects
(Customer, CateringLead, Quote, a repriced dict) don't exist in telephony:
- The reference's `id` (a DB order_id) becomes call_uuid here -- telephony
  has no numeric order id, call_uuid is the closest stable identifier.
- The reference's `caller` (customer.phone_number) becomes the caller's
  number from telephony's own call state.
- metadata.pickup_time in the reference actually holds
  `lead.delivery_or_pickup` (a "pickup"/"delivery" string, despite the
  field's name -- not a clock time). Mapped here to order["fulfillment"],
  which carries the same semantic meaning in the new stack.
- metadata.takeaway_details in the reference was a note specific to the
  catering-approval flow ("revised approval | no bell"). There's no
  equivalent concept in telephony's order flow, so this is set to a
  plain order_type description instead -- if the real print service
  expects something more specific here, that's the one field most likely
  to need adjusting once it's confirmed against the live service.
"""
import logging

import httpx

import config

logger = logging.getLogger(__name__)


def _map_items(order_items: list[dict]) -> list[dict]:
    """order["items"] shape (chat_manager/emitter.py) ->
    reference's items shape (dashboard.py:319-329)."""
    mapped = []
    for item in order_items or []:
        qty = int(item.get("quantity") or 1)
        mapped.append({
            "name": item.get("name", "Item"),
            "quantity": qty,
            "qty": qty,  # duplicate key, kept per reference
            "price": item.get("line_total", 0),  # reference's "price" == line total
            "unit_price": item.get("unit_price", 0),
        })
    return mapped


def print_order(order: dict, *, call_uuid: str, caller: str, order_type: str | None = None) -> bool:
    """Fire-and-forget a kitchen ticket. Returns True if the POST appeared
    to succeed, False otherwise -- callers should not branch on this
    return value for anything call-critical. It exists for logging only.
    """
    if not config.PRINT_API_URL:
        logger.debug("PRINT_API_URL not set, skipping print for call_uuid=%s", call_uuid)
        return False

    payload = {
        "id": call_uuid,
        "caller": caller,
        "items": _map_items(order.get("items", [])),
        "subtotal": order.get("subtotal", 0),
        "tax": order.get("tax", 0),
        "total": order.get("total", 0),
        "metadata": {
            "customer_name": order.get("customer_name", ""),
            "pickup_time": order.get("fulfillment", ""),
            "takeaway_details": f"{order_type or 'pickup'} order",
        },
    }

    headers = {}
    if config.PRINT_API_KEY:
        headers["Authorization"] = f"Bearer {config.PRINT_API_KEY}"

    try:
        r = httpx.post(
            f"{config.PRINT_API_URL.rstrip('/')}/print/order",
            json=payload,
            headers=headers,
            timeout=5.0,
            verify=True,  # Enforce TLS verification
        )
        r.raise_for_status()
        logger.info("print request sent call_uuid=%s status=%s", call_uuid, r.status_code)
        return True
    except Exception:
        # Matches the reference's bare except: pass -- a printer failure
        # must never propagate into /voice/hangup.
        logger.exception("print request failed call_uuid=%s (non-fatal, call continues)", call_uuid)
        return False


def print_manager_request(
    reply: dict, *, call_uuid: str, caller: str
) -> bool:
    """Print a quote-free cake/catering callback sheet.

    The production printer service already understands this internal shape and
    deliberately renders it without prices. Delivery redirects are not manager
    requests and must never use this path.
    """
    if not config.PRINT_API_URL:
        logger.debug(
            "PRINT_API_URL not set, skipping manager sheet for call_uuid=%s",
            call_uuid,
        )
        return False

    order_type = reply.get("order_type") or "catering"
    transcript = reply.get("verbatim_user_chat") or []
    details = reply.get("summary", "")
    if transcript:
        details = "\n".join(
            part for part in (details, "Caller said: " + " | ".join(map(str, transcript)))
            if part
        )
    payload = {
        "id": call_uuid,
        "caller": caller,
        "items": [],
        "subtotal": 0,
        "tax": 0,
        "total": 0,
        "metadata": {
            "customer_name": reply.get("name") or "no_name_given",
            "pickup_time": "",
            "takeaway_details": details,
            "document_type": "manager_request",
            "order_type": order_type,
            "quote_free": True,
        },
    }
    headers = {}
    if config.PRINT_API_KEY:
        headers["Authorization"] = f"Bearer {config.PRINT_API_KEY}"

    try:
        r = httpx.post(
            f"{config.PRINT_API_URL.rstrip('/')}/print/order",
            json=payload,
            headers=headers,
            timeout=5.0,
            verify=True,  # Enforce TLS verification
        )
        r.raise_for_status()
        logger.info(
            "manager print request sent call_uuid=%s status=%s",
            call_uuid,
            r.status_code,
        )
        return True
    except Exception:
        logger.exception(
            "manager print request failed call_uuid=%s (non-fatal, call continues)",
            call_uuid,
        )
        return False
