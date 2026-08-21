import config
import print_client


class _Response:
    status_code = 200

    def raise_for_status(self):
        return None


def test_pickup_payload_matches_production_contract(monkeypatch):
    monkeypatch.setattr(config, "PRINT_API_URL", "https://printer.test/")
    seen = {}

    def post(url, **kwargs):
        seen.update(url=url, **kwargs)
        return _Response()

    monkeypatch.setattr(print_client.httpx, "post", post)
    assert print_client.print_order(
        {
            "customer_name": "Anjali",
            "fulfillment": "pickup",
            "items": [{"name": "Samosa", "quantity": 2, "unit_price": "2.00", "line_total": "4.00"}],
            "subtotal": "4.00", "tax": "0.31", "total": "4.31",
        },
        call_uuid="call-1", caller="+15550001111", order_type="pickup",
    )
    assert seen["url"] == "https://printer.test/print/order"
    assert seen["timeout"] == 5.0
    assert seen["json"]["items"][0] == {
        "name": "Samosa", "quantity": 2, "qty": 2,
        "price": "4.00", "unit_price": "2.00",
    }
    assert seen["json"]["metadata"]["customer_name"] == "Anjali"


def test_manager_sheet_is_quote_free_and_keeps_details(monkeypatch):
    monkeypatch.setattr(config, "PRINT_API_URL", "https://printer.test")
    seen = {}
    monkeypatch.setattr(
        print_client.httpx, "post",
        lambda url, **kwargs: (seen.update(url=url, **kwargs) or _Response()),
    )
    assert print_client.print_manager_request(
        {
            "order_type": "cake/catering", "name": "Maya",
            "summary": "Birthday catering for 100 guests.",
            "verbatim_user_chat": ["I would like a three-tier cake."],
        },
        call_uuid="call-2", caller="+15550002222",
    )
    metadata = seen["json"]["metadata"]
    assert metadata["document_type"] == "manager_request"
    assert metadata["quote_free"] is True
    assert metadata["customer_name"] == "Maya"
    assert "three-tier cake" in metadata["takeaway_details"]
    assert seen["json"]["total"] == 0


def test_printer_failure_is_never_call_fatal(monkeypatch):
    monkeypatch.setattr(config, "PRINT_API_URL", "https://printer.test")
    monkeypatch.setattr(
        print_client.httpx, "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("offline")),
    )
    assert not print_client.print_order({}, call_uuid="call-3", caller="")
    assert not print_client.print_manager_request({}, call_uuid="call-4", caller="")
