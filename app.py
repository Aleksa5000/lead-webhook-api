import csv
import hmac
import io
import logging
import os
import re

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, EmailStr, Field, field_validator

import db

log = logging.getLogger("lead-webhook-api")
app = FastAPI(title="Lead Webhook API", version="1.0.0")

FREE_DOMAINS = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com", "aol.com"}
KEYWORDS = {"budget": 15, "urgent": 10, "quote": 10, "asap": 5, "contract": 5}
NOTIFY_THRESHOLD = 50


class Lead(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    phone: str | None = None
    message: str = Field(min_length=1, max_length=5000)
    source: str = Field(default="unknown", max_length=100)

    @field_validator("email")
    @classmethod
    def lower_email(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("phone")
    @classmethod
    def clean_phone(cls, v: str | None) -> str | None:
        if not v:
            return None
        digits = re.sub(r"\D", "", v)
        if not 7 <= len(digits) <= 15:
            raise ValueError("phone must have 7-15 digits")
        return ("+" if v.strip().startswith("+") else "") + digits


def score_lead(lead: Lead) -> int:
    """0-100: business domain, phone given, message length, buying-intent keywords."""
    score = 10 if lead.email.split("@")[1] in FREE_DOMAINS else 35
    score += 15 if lead.phone else 0
    score += min(len(lead.message) // 10, 25)
    text = lead.message.lower()
    score += sum(pts for kw, pts in KEYWORDS.items() if kw in text)
    return min(score, 100)


def check_secret(x_webhook_secret: str | None = Header(default=None)) -> None:
    secret = os.getenv("WEBHOOK_SECRET")
    if secret and not hmac.compare_digest((x_webhook_secret or "").encode(), secret.encode()):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Webhook-Secret")


def notify_slack(lead: dict) -> None:
    url = os.getenv("SLACK_WEBHOOK_URL")
    if not url:
        return
    text = (f":fire: New hot lead (score {lead['score']}) from {lead['source']}\n"
            f"*{lead['name']}* <{lead['email']}> {lead['phone'] or ''}\n>{lead['message'][:300]}")
    try:
        httpx.post(url, json={"text": text}, timeout=5).raise_for_status()
    except Exception:
        log.exception("Slack notification failed")


@app.post("/webhooks/lead", status_code=201, dependencies=[Depends(check_secret)])
def receive_lead(lead: Lead, background: BackgroundTasks):
    if db.is_duplicate(lead.email):
        return JSONResponse({"status": "duplicate"}, status_code=200)
    stored = db.insert_lead(lead.name, lead.email, lead.phone, lead.message,
                            lead.source, score_lead(lead))
    if stored["score"] >= NOTIFY_THRESHOLD:
        background.add_task(notify_slack, stored)
    return {"status": "created", "id": stored["id"], "score": stored["score"]}


@app.get("/leads")
def leads(min_score: int = Query(0, ge=0, le=100)):
    return db.list_leads(min_score)


@app.get("/leads/export.csv")
def export_csv():
    buf = io.StringIO()
    fields = ["id", "name", "email", "phone", "message", "source", "score", "created_at"]
    w = csv.DictWriter(buf, fieldnames=fields)
    w.writeheader()
    w.writerows(db.list_leads())
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=leads.csv"})


@app.get("/health")
def health():
    return {"status": "ok"}
