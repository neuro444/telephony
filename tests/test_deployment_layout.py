"""Deployment paths must not shadow importable application packages."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_compose_mounts_mutable_data_outside_application_packages():
    compose = (ROOT / "docker-compose.yml").read_text()

    assert "gateway_audio:/data/audio" in compose
    assert "gateway_orders:/data/orders" in compose
    assert "gateway_cost:/data/cost" in compose
    assert "gateway_orders:/app/orders" not in compose
    assert "gateway_cost:/app/cost" not in compose


def test_example_environment_uses_data_mount_paths():
    example = (ROOT / ".env.example").read_text()

    assert "AUDIO_DIR=/data/audio" in example
    assert "ORDERS_LOG_PATH=/data/orders/orders.jsonl" in example
    assert "COST_LOG_PATH=/data/cost/costs.jsonl" in example
