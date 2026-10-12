import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app as app_module  # noqa: E402

LEAD = {"name": "Jane Doe", "email": "Jane@Acme.com", "phone": "+1 (555) 123-4567",
        "message": "We have a budget and need this urgent, please send a quote.",
        "source": "typeform"}
COLD = {"name": "x", "email": "x@gmail.com", "message": "hi"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "t.db"))
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    return TestClient(app_module.app)


def test_valid_lead_stored_and_normalized(client):
    assert client.post("/webhooks/lead", json=LEAD).status_code == 201
    row = client.get("/leads").json()[0]
    assert row["email"] == "jane@acme.com"
    assert row["phone"] == "+15551234567"


def test_invalid_email_422(client):
    assert client.post("/webhooks/lead", json={**LEAD, "email": "nope"}).status_code == 422


def test_secret(client, monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "s3cret")
    assert client.post("/webhooks/lead", json=LEAD).status_code == 401
    assert client.post("/webhooks/lead", json=LEAD, headers={"X-Webhook-Secret": "bad"}).status_code == 401
    assert client.post("/webhooks/lead", json=LEAD, headers={"X-Webhook-Secret": "s3cret"}).status_code == 201


def test_duplicate(client):
    client.post("/webhooks/lead", json=LEAD)
    r = client.post("/webhooks/lead", json={**LEAD, "email": "JANE@acme.com"})
    assert r.status_code == 200 and r.json() == {"status": "duplicate"}
    assert len(client.get("/leads").json()) == 1


def test_scoring():
    hot, cold = app_module.Lead(**LEAD), app_module.Lead(**COLD)
    assert app_module.score_lead(hot) >= 50 > app_module.score_lead(cold)
    assert 0 <= app_module.score_lead(hot) <= 100


def test_min_score_filter(client):
    client.post("/webhooks/lead", json=LEAD)
    client.post("/webhooks/lead", json=COLD)
    assert len(client.get("/leads").json()) == 2
    assert len(client.get("/leads?min_score=50").json()) == 1


def test_csv_export(client):
    client.post("/webhooks/lead", json=LEAD)
    r = client.get("/leads/export.csv")
    assert r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("id,name,email") and "jane@acme.com" in lines[1]


def test_slack_called_for_hot_lead_only(client, monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.test/x")
    calls = []

    class Resp:
        def raise_for_status(self):
            pass

    monkeypatch.setattr(app_module.httpx, "post", lambda url, **kw: calls.append((url, kw)) or Resp())
    client.post("/webhooks/lead", json=LEAD)
    client.post("/webhooks/lead", json=COLD)
    assert len(calls) == 1 and "Jane Doe" in calls[0][1]["json"]["text"]


def test_slack_failure_does_not_break_request(client, monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.test/x")

    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(app_module.httpx, "post", boom)
    assert client.post("/webhooks/lead", json=LEAD).status_code == 201


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}
