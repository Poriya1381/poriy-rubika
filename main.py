import os, math, secrets
from datetime import datetime, timezone
from typing import Optional
from fastapi import FastAPI, Depends, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, String, Integer, DateTime, Text, select, desc
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, Session

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./poriy.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://") and "+psycopg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
opts = {"pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite"):
    opts["connect_args"] = {"check_same_thread": False}
engine = create_engine(DATABASE_URL, **opts)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

class Base(DeclarativeBase): pass
class Service(Base):
    __tablename__ = "services"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(160))
    category: Mapped[str] = mapped_column(String(80), default="روبیکا")
    description: Mapped[str] = mapped_column(Text, default="")
    price_per_1000: Mapped[int] = mapped_column(Integer, default=0)
    min_quantity: Mapped[int] = mapped_column(Integer, default=1)
    max_quantity: Mapped[int] = mapped_column(Integer, default=100000)
    enabled: Mapped[int] = mapped_column(Integer, default=1)
class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_code: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    service_code: Mapped[str] = mapped_column(String(80))
    service_title: Mapped[str] = mapped_column(String(160))
    target: Mapped[str] = mapped_column(String(500))
    quantity: Mapped[int] = mapped_column(Integer)
    amount_toman: Mapped[int] = mapped_column(Integer)
    customer_contact: Mapped[str] = mapped_column(String(160), default="")
    payment_reference: Mapped[str] = mapped_column(String(160), default="")
    status: Mapped[str] = mapped_column(String(40), default="awaiting_payment")
    admin_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
class TargetSnapshot(Base):
    __tablename__ = "target_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target: Mapped[str] = mapped_column(String(500), index=True)
    follower_count: Mapped[int] = mapped_column(Integer)
    source_note: Mapped[str] = mapped_column(String(250), default="ثبت دستی مدیر")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
Base.metadata.create_all(engine)
app = FastAPI(title="Poriy Rubika Services API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=[os.getenv("FRONTEND_ORIGIN", "https://poriya1381.github.io"), "http://localhost:8000", "http://127.0.0.1:8000"], allow_credentials=False, allow_methods=["GET", "POST", "PATCH", "OPTIONS"], allow_headers=["Content-Type", "X-Admin-Key"])
def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()
def admin_guard(x_admin_key: Optional[str] = Header(default=None)):
    expected = os.getenv("ADMIN_KEY", "")
    if not expected or not x_admin_key or not secrets.compare_digest(x_admin_key, expected):
        raise HTTPException(status_code=401, detail="کلید مدیر نامعتبر است")
class ServiceInput(BaseModel):
    code: str = Field(min_length=2, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    title: str = Field(min_length=2, max_length=160)
    category: str = Field(default="روبیکا", max_length=80)
    description: str = Field(default="", max_length=2000)
    price_per_1000: int = Field(ge=0, le=1000000000)
    min_quantity: int = Field(default=1, ge=1, le=100000000)
    max_quantity: int = Field(default=100000, ge=1, le=100000000)
    enabled: bool = True
class OrderInput(BaseModel):
    service_code: str
    target: str = Field(min_length=2, max_length=500)
    quantity: int = Field(ge=1, le=100000000)
    customer_contact: str = Field(default="", max_length=160)
class PaymentInput(BaseModel): payment_reference: str = Field(min_length=2, max_length=160)
class OrderStatusInput(BaseModel): status: str; admin_note: str = ""
class SnapshotInput(BaseModel):
    target: str = Field(min_length=2, max_length=500)
    follower_count: int = Field(ge=0, le=1000000000)
    source_note: str = Field(default="ثبت دستی مدیر", max_length=250)
STATUSES = {"awaiting_payment", "payment_review", "payment_approved", "processing", "completed", "rejected", "cancelled"}
def order_json(o):
    return {"id":o.id,"code":o.public_code,"service_code":o.service_code,"service_title":o.service_title,"target":o.target,"quantity":o.quantity,"amount_toman":o.amount_toman,"customer_contact":o.customer_contact,"payment_reference":o.payment_reference,"status":o.status,"admin_note":o.admin_note,"created_at":o.created_at.isoformat() if o.created_at else None}
@app.get("/")
def root(): return {"service":"Poriy Rubika Services API","status":"online","docs":"/docs"}
@app.get("/api/health")
def health(): return {"ok":True}
@app.get("/api/config")
def config(): return {"card_holder":os.getenv("CARD_HOLDER",""),"card_number":os.getenv("CARD_NUMBER",""),"payment_method":"انتقال کارت‌به‌کارت با بررسی دستی مدیر"}
@app.get("/api/services")
def services(db: Session=Depends(get_db)):
    rows=db.scalars(select(Service).where(Service.enabled==1).order_by(Service.category,Service.id)).all()
    return [{"code":s.code,"title":s.title,"category":s.category,"description":s.description,"price_per_1000":s.price_per_1000,"min_quantity":s.min_quantity,"max_quantity":s.max_quantity} for s in rows]
@app.post("/api/orders")
def create_order(data: OrderInput, db: Session=Depends(get_db)):
    s=db.scalar(select(Service).where(Service.code==data.service_code,Service.enabled==1))
    if not s: raise HTTPException(404,"سرویس پیدا نشد یا غیرفعال است")
    if not s.min_quantity <= data.quantity <= s.max_quantity: raise HTTPException(400,f"تعداد باید بین {s.min_quantity} و {s.max_quantity} باشد")
    amount=math.ceil(data.quantity*s.price_per_1000/1000)
    if amount<=0: raise HTTPException(400,"قیمت سرویس هنوز تنظیم نشده است")
    o=Order(public_code=secrets.token_hex(5).upper(),service_code=s.code,service_title=s.title,target=data.target.strip(),quantity=data.quantity,amount_toman=amount,customer_contact=data.customer_contact.strip())
    db.add(o); db.commit(); db.refresh(o); return order_json(o)
@app.post("/api/orders/{code}/payment")
def payment(code: str, data: PaymentInput, db: Session=Depends(get_db)):
    o=db.scalar(select(Order).where(Order.public_code==code))
    if not o: raise HTTPException(404,"سفارش پیدا نشد")
    if o.status not in {"awaiting_payment","payment_review"}: raise HTTPException(400,"این سفارش در انتظار ثبت پرداخت نیست")
    o.payment_reference=data.payment_reference.strip(); o.status="payment_review"; db.commit(); db.refresh(o); return order_json(o)
@app.get("/api/orders/{code}")
def get_order(code: str, db: Session=Depends(get_db)):
    o=db.scalar(select(Order).where(Order.public_code==code))
    if not o: raise HTTPException(404,"سفارش پیدا نشد")
    return order_json(o)
@app.get("/api/targets/history")
def history(target: str=Query(min_length=2,max_length=500),db: Session=Depends(get_db)):
    rows=db.scalars(select(TargetSnapshot).where(TargetSnapshot.target==target).order_by(desc(TargetSnapshot.created_at)).limit(100)).all()
    return [{"target":r.target,"follower_count":r.follower_count,"source_note":r.source_note,"created_at":r.created_at.isoformat() if r.created_at else None} for r in rows]
@app.get("/api/targets/current-count")
def current_count(target: str=Query(min_length=2,max_length=500),db: Session=Depends(get_db)):
    r=db.scalar(select(TargetSnapshot).where(TargetSnapshot.target==target).order_by(desc(TargetSnapshot.created_at)).limit(1))
    if not r: raise HTTPException(404,"هنوز آماری برای این صفحه ثبت نشده است")
    return {"target":r.target,"follower_count":r.follower_count,"created_at":r.created_at.isoformat() if r.created_at else None}
@app.post("/api/admin/services",dependencies=[Depends(admin_guard)])
def upsert_service(data: ServiceInput,db: Session=Depends(get_db)):
    if data.max_quantity<data.min_quantity: raise HTTPException(400,"حداکثر تعداد باید از حداقل بیشتر باشد")
    s=db.scalar(select(Service).where(Service.code==data.code))
    if s is None: s=Service(code=data.code,title=data.title,category=data.category); db.add(s)
    for k,v in data.model_dump().items(): setattr(s,k, (1 if v else 0) if k=="enabled" else v)
    db.commit(); return {"ok":True,"service":data.model_dump()}
@app.get("/api/admin/orders",dependencies=[Depends(admin_guard)])
def admin_orders(status: Optional[str]=None,limit: int=Query(default=100,ge=1,le=500),db: Session=Depends(get_db)):
    q=select(Order).order_by(desc(Order.created_at)).limit(limit)
    if status: q=select(Order).where(Order.status==status).order_by(desc(Order.created_at)).limit(limit)
    return [order_json(o) for o in db.scalars(q).all()]
@app.patch("/api/admin/orders/{code}",dependencies=[Depends(admin_guard)])
def update_order(code: str,data: OrderStatusInput,db: Session=Depends(get_db)):
    if data.status not in STATUSES: raise HTTPException(400,"وضعیت نامعتبر است")
    o=db.scalar(select(Order).where(Order.public_code==code))
    if not o: raise HTTPException(404,"سفارش پیدا نشد")
    o.status=data.status; o.admin_note=data.admin_note[:2000]; db.commit(); db.refresh(o); return order_json(o)
@app.post("/api/admin/targets/snapshot",dependencies=[Depends(admin_guard)])
def snapshot(data: SnapshotInput,db: Session=Depends(get_db)):
    r=TargetSnapshot(target=data.target.strip(),follower_count=data.follower_count,source_note=data.source_note); db.add(r); db.commit(); db.refresh(r); return {"ok":True,"id":r.id}
@app.get("/api/admin/stats",dependencies=[Depends(admin_guard)])
def stats(db: Session=Depends(get_db)):
    orders=db.scalars(select(Order)).all()
    return {"total_orders":len(orders),"waiting_payment":sum(o.status=="awaiting_payment" for o in orders),"payment_review":sum(o.status=="payment_review" for o in orders),"processing":sum(o.status=="processing" for o in orders),"completed":sum(o.status=="completed" for o in orders),"total_toman":sum(o.amount_toman for o in orders if o.status in {"payment_approved","processing","completed"})}
