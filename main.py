import os, hmac, hashlib, sqlite3, uuid
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import httpx

DB = os.getenv("DB_PATH", "orders.db")
VARIZA_API_KEY = os.getenv("VARIZA_API_KEY", "")
VARIZA_WEBHOOK_SECRET = os.getenv("VARIZA_WEBHOOK_SECRET", "")
PUBLIC_FRONTEND = os.getenv("PUBLIC_FRONTEND", "https://YOUR-USER.github.io/YOUR-REPO/")
VARIZA_RETURN_URL = os.getenv("VARIZA_RETURN_URL", PUBLIC_FRONTEND)
VARIZA_API = "https://variza.ir/api/v1/pay"

# قیمت پایه هر واحد به تومان؛ قبل از انتشار تنظیم کنید.
PRICES = {"member": 200, "view": 100}
FOLLOWER_TIERS = [
    (10_000, 40_000),
    (20_000, 75_000),
    (30_000, 105_000),
    (50_000, 160_000),
    (100_000, 260_000),
    (150_000, 320_000),
    (200_000, 350_000),
    (250_000, 375_000),
    (300_000, 390_000),
    (330_000, 400_000),
]
def follower_price(qty: int) -> int:
    if qty < 10_000 or qty > 330_000 or qty % 10_000 != 0:
        raise ValueError("فالور فقط از 10K تا 330K و مضرب 10K قابل خرید است.")
    for q, p in FOLLOWER_TIERS:
        if qty == q:
            return p
    return 400_000

app = FastAPI(title="Rubika Shop API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    c=db()
    c.execute("""CREATE TABLE IF NOT EXISTS orders(
      id TEXT PRIMARY KEY, service TEXT, target TEXT, quantity INTEGER, amount INTEGER,
      status TEXT, reserved_from INTEGER, reserved_to INTEGER, payment_slug TEXT,
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("""CREATE TABLE IF NOT EXISTS inventory(
      service TEXT PRIMARY KEY, total INTEGER NOT NULL, next_position INTEGER NOT NULL)""")
    for service,total in [("follower",320000),("member",0),("view",0)]:
        c.execute("INSERT OR IGNORE INTO inventory(service,total,next_position) VALUES(?,?,0)",(service,total))
    c.commit(); c.close()
init_db()

class OrderIn(BaseModel):
    service: str
    target: str = Field(min_length=1, max_length=500)
    quantity: int = Field(gt=0, le=320000)
    phone: str | None = None

def reserve(service, qty):
    c=db()
    try:
        c.execute("BEGIN IMMEDIATE")
        row=c.execute("SELECT total,next_position FROM inventory WHERE service=?",(service,)).fetchone()
        if not row or row["total"] <= 0: raise ValueError("این سرویس موجودی فعال ندارد.")
        start=row["next_position"]; end=start+qty
        if end > row["total"]: raise ValueError(f"موجودی کافی نیست؛ باقی‌مانده {row['total']-start}")
        c.execute("UPDATE inventory SET next_position=? WHERE service=?",(end,service))
        c.commit(); return start,end
    except:
        c.rollback(); raise
    finally: c.close()

@app.get("/api/health")
def health(): return {"ok": True}

@app.post("/api/orders")
async def create_order(o: OrderIn):
    if o.service not in PRICES: raise HTTPException(400,"سرویس نامعتبر")
    if not VARIZA_API_KEY: raise HTTPException(500,"VARIZA_API_KEY تنظیم نشده")
    amount=o.quantity*PRICES[o.service]
    try: start,end=reserve(o.service,o.quantity)
    except ValueError as e: raise HTTPException(400,str(e))
    oid="RB-"+uuid.uuid4().hex[:10].upper()
    c=db(); c.execute("INSERT INTO orders(id,service,target,quantity,amount,status,reserved_from,reserved_to) VALUES(?,?,?,?,?,?,?,?)",
      (oid,o.service,o.target,o.quantity,amount,"awaiting_payment",start,end)); c.commit(); c.close()
    payload={"amount":amount,"return_url":VARIZA_RETURN_URL,"title":f"Order {oid}","expires_in":"1h"}
    try:
        async with httpx.AsyncClient(timeout=20) as x:
            r=await x.post(VARIZA_API,headers={"Authorization":f"Bearer {VARIZA_API_KEY}"},json=payload)
        if r.status_code>=300: raise Exception(r.text)
        data=r.json()
        c=db(); c.execute("UPDATE orders SET payment_slug=? WHERE id=?",(data["slug"],oid)); c.commit(); c.close()
        return {"order_id":oid,"pay_url":data["pay_url"],"amount":amount}
    except Exception as e:
        c=db(); c.execute("UPDATE orders SET status='payment_link_failed' WHERE id=?",(oid,)); c.commit(); c.close()
        raise HTTPException(502,"ساخت لینک پرداخت ناموفق بود")

@app.post("/webhook/variza")
async def variza_webhook(request: Request):
    raw=await request.body()
    sig=request.headers.get("X-Webhook-Signature","")
    if not VARIZA_WEBHOOK_SECRET or not sig:
        raise HTTPException(400,"signature missing")
    expected="sha256="+hmac.new(VARIZA_WEBHOOK_SECRET.encode(),raw,hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig,expected): raise HTTPException(400,"bad signature")
    data=await request.json()
    if data.get("event")!="payment.paid" or data.get("status")!="paid":
        return {"ok":True}
    slug=data.get("slug"); amount=int(data.get("base_amount",data.get("amount",0)))
    c=db(); order=c.execute("SELECT * FROM orders WHERE payment_slug=?",(slug,)).fetchone()
    if not order: c.close(); return {"ok":True}
    if order["status"]=="paid": c.close(); return {"ok":True}
    if amount < order["amount"]:
        c.close(); raise HTTPException(400,"amount mismatch")
    c.execute("UPDATE orders SET status='paid' WHERE id=?",(order["id"],)); c.commit(); c.close()
    # Worker مجاز/رسمی می‌تواند سفارش‌های status=paid را از دیتابیس بردارد.
    return {"ok":True}

@app.get("/api/orders/{order_id}")
def get_order(order_id:str):
    c=db(); row=c.execute("SELECT id,service,target,quantity,amount,status,reserved_from,reserved_to,created_at FROM orders WHERE id=?",(order_id,)).fetchone(); c.close()
    if not row: raise HTTPException(404,"سفارش پیدا نشد")
    return dict(row)
