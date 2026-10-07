import os
import hmac
import hashlib
import sqlite3
import uuid

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import httpx


DB = os.getenv("DB_PATH", "orders.db")

VARIZA_API_KEY = os.getenv("VARIZA_API_KEY", "")
VARIZA_WEBHOOK_SECRET = os.getenv("VARIZA_WEBHOOK_SECRET", "")

PUBLIC_FRONTEND = os.getenv(
    "PUBLIC_FRONTEND",
    "https://poriya1381.github.io/poriy-rubika/"
)

VARIZA_RETURN_URL = os.getenv(
    "VARIZA_RETURN_URL",
    PUBLIC_FRONTEND
)

VARIZA_API = "https://variza.ir/api/v1/pay"


FOLLOWER_TIERS = {
    10_000: 40_000,
    20_000: 75_000,
    30_000: 105_000,
    50_000: 160_000,
    100_000: 260_000,
    150_000: 320_000,
    200_000: 350_000,
    250_000: 375_000,
    300_000: 390_000,
    330_000: 400_000,
}


def follower_price(quantity: int) -> int:
    if quantity not in FOLLOWER_TIERS:
        raise ValueError(
            "تعداد فالوور باید یکی از بسته‌های 10K، 20K، 30K، "
            "50K، 100K، 150K، 200K، 250K، 300K یا 330K باشد."
        )

    return FOLLOWER_TIERS[quantity]


app = FastAPI(title="PORIY SERVICES API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS orders(
            id TEXT PRIMARY KEY,
            service TEXT NOT NULL,
            target TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            status TEXT NOT NULL,
            reserved_from INTEGER,
            reserved_to INTEGER,
            payment_slug TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS inventory(
            service TEXT PRIMARY KEY,
            total INTEGER NOT NULL,
            next_position INTEGER NOT NULL
        )
    """)

    conn.execute("""
        INSERT OR IGNORE INTO inventory
        (service, total, next_position)
        VALUES (?, ?, ?)
    """, ("followers", 330_000, 0))

    conn.commit()
    conn.close()


init_db()


class OrderIn(BaseModel):
    service: str
    target: str = Field(min_length=1, max_length=500)
    quantity: int = Field(gt=0, le=330000)
    phone: str | None = None


def reserve_followers(quantity: int):
    conn = db()

    try:
        conn.execute("BEGIN IMMEDIATE")

        row = conn.execute(
            """
            SELECT total, next_position
            FROM inventory
            WHERE service = ?
            """,
            ("followers",)
        ).fetchone()

        if not row:
            raise ValueError("موجودی فالوور تعریف نشده است.")

        total = int(row["total"])
        current = int(row["next_position"])

        if current >= total:
            raise ValueError("موجودی فالوور تمام شده است.")

        end = current + quantity

        if end > total:
            remaining = total - current
            raise ValueError(
                f"موجودی کافی نیست؛ موجودی باقی‌مانده: {remaining:,}"
            )

        conn.execute(
            """
            UPDATE inventory
            SET next_position = ?
            WHERE service = ?
            """,
            (end, "followers")
        )

        conn.commit()

        return current, end

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "service": "PORIY SERVICES"
    }


@app.post("/api/orders")
async def create_order(order: OrderIn):

    if order.service != "followers":
        raise HTTPException(
            status_code=400,
            detail="این سرویس هنوز فعال نشده است."
        )

    try:
        amount = follower_price(order.quantity)
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=str(e)
        )

    if not VARIZA_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="VARIZA_API_KEY تنظیم نشده است."
        )

    try:
        reserved_from, reserved_to = reserve_followers(
            order.quantity
        )
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=str(e)
        )

    order_id = "RB-" + uuid.uuid4().hex[:10].upper()

    conn = db()

    conn.execute(
        """
        INSERT INTO orders
        (
            id,
            service,
            target,
            quantity,
            amount,
            status,
            reserved_from,
            reserved_to
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            order_id,
            order.service,
            order.target,
            order.quantity,
            amount,
            "awaiting_payment",
            reserved_from,
            reserved_to
        )
    )

    conn.commit()
    conn.close()

    payload = {
        "amount": amount,
        "return_url": VARIZA_RETURN_URL,
        "title": f"PORIY SERVICES - {order_id}",
        "expires_in": "1h"
    }

    try:
        async with httpx.AsyncClient(timeout=20) as client:

            response = await client.post(
                VARIZA_API,
                headers={
                    "Authorization": f"Bearer {VARIZA_API_KEY}",
                    "Content-Type": "application/json"
                },
                json=payload
            )

        if response.status_code >= 300:

            print(
                "VARIZA ERROR:",
                response.status_code,
                response.text
            )

            conn = db()

            conn.execute(
                """
                UPDATE orders
                SET status = ?
                WHERE id = ?
                """,
                ("payment_link_failed", order_id)
            )

            conn.commit()
            conn.close()

            raise HTTPException(
                status_code=502,
                detail="واریزا نتوانست لینک پرداخت ایجاد کند."
            )

        data = response.json()

        payment_slug = data.get("slug")
        pay_url = data.get("pay_url")

        if not payment_slug or not pay_url:

            print(
                "INVALID VARIZA RESPONSE:",
                data
            )

            conn = db()

            conn.execute(
                """
                UPDATE orders
                SET status = ?
                WHERE id = ?
                """,
                ("payment_link_failed", order_id)
            )

            conn.commit()
            conn.close()

            raise HTTPException(
                status_code=502,
                detail="پاسخ واریزا نامعتبر است."
            )

        conn = db()

        conn.execute(
            """
            UPDATE orders
            SET payment_slug = ?
            WHERE id = ?
            """,
            (payment_slug, order_id)
        )

        conn.commit()
        conn.close()

        return {
            "ok": True,
            "order_id": order_id,
            "pay_url": pay_url,
            "amount": amount
        }

    except HTTPException:
        raise

    except Exception as e:

        print(
            "PAYMENT ERROR:",
            repr(e)
        )

        conn = db()

        conn.execute(
            """
            UPDATE orders
            SET status = ?
            WHERE id = ?
            """,
            ("payment_link_failed", order_id)
        )

        conn.commit()
        conn.close()

        raise HTTPException(
            status_code=502,
            detail="خطا هنگام اتصال به واریزا."
        )


@app.post("/webhook/variza")
async def variza_webhook(request: Request):

    raw_body = await request.body()

    signature = request.headers.get(
        "X-Webhook-Signature",
        ""
    )

    if not VARIZA_WEBHOOK_SECRET:
        raise HTTPException(
            status_code=500,
            detail="VARIZA_WEBHOOK_SECRET تنظیم نشده است."
        )

    if not signature:
        raise HTTPException(
            status_code=400,
            detail="Webhook signature missing"
        )

    expected = (
        "sha256="
        + hmac.new(
            VARIZA_WEBHOOK_SECRET.encode(),
            raw_body,
            hashlib.sha256
        ).hexdigest()
    )

    if not hmac.compare_digest(
        signature,
        expected
    ):
        raise HTTPException(
            status_code=400,
            detail="Webhook signature invalid"
        )

    try:
        data = await request.json()

    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid JSON"
        )

    if data.get("event") != "payment.paid":
        return {"ok": True}

    if data.get("status") != "paid":
        return {"ok": True}

    slug = data.get("slug")

    if not slug:
        return {"ok": True}

    try:
        paid_amount = int(
            data.get(
                "base_amount",
                data.get("amount", 0)
            )
        )

    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid payment amount"
        )

    conn = db()

    order = conn.execute(
        """
        SELECT *
        FROM orders
        WHERE payment_slug = ?
        """,
        (slug,)
    ).fetchone()

    if not order:
        conn.close()
        return {"ok": True}

    if order["status"] == "paid":
        conn.close()
        return {"ok": True}

    if paid_amount < int(order["amount"]):
        conn.close()

        raise HTTPException(
            status_code=400,
            detail="مبلغ پرداختی کمتر از مبلغ سفارش است."
        )

    conn.execute(
        """
        UPDATE orders
        SET status = ?
        WHERE id = ?
        """,
        ("paid", order["id"])
    )

    conn.commit()
    conn.close()

    return {
        "ok": True
    }


@app.get("/api/orders/{order_id}")
def get_order(order_id: str):

    conn = db()

    row = conn.execute(
        """
        SELECT
            id,
            service,
            target,
            quantity,
            amount,
            status,
            reserved_from,
            reserved_to,
            payment_slug,
            created_at
        FROM orders
        WHERE id = ?
        """,
        (order_id,)
    ).fetchone()

    conn.close()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="سفارش پیدا نشد."
        )

    return dict(row)
