from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError

from .db import database
from .engine_bridge import SearchService, seed
from .models import (Notification, PriceAlert, PriceListing, Product, PushSubscription,
                     ScrapingJob, SearchHistory, User, Vendor, now)
from .schemas import (AlertInput, AlertPatch, CATEGORIES, ConfigPatch, Credentials,
                      PasswordChange, PushInput, SearchInput)
from .security import admin, current_user, dummy_hash, passwords, token
from .settings import Settings


def record(row, exclude=()):
    return {col.name: getattr(row, col.name) for col in row.__table__.columns if col.name not in exclude}


def owned(db, model, identity, user):
    row = db.get(model, identity)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "Not found")
    return row


def create_app(settings=None):
    settings = settings or Settings()
    engine, sessions = database(settings.database_url)

    @asynccontextmanager
    async def lifespan(app):
        # Deliberately do not create/alter production schema on web startup.
        seed(sessions)
        app.state.search = SearchService(sessions, settings)
        yield
        engine.dispose()

    app = FastAPI(title="Cheapest Price Finder Backend", version="1.0.0", lifespan=lifespan,
                  description="Backend for the existing Python engine. All prices are INR. "
                              "Fixture mode returns synthetic demo data.")
    app.state.settings, app.state.sessions, app.state.db_engine = settings, sessions, engine
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                       allow_methods=["GET", "POST", "PATCH", "DELETE"],
                       allow_headers=["Authorization", "Content-Type"])

    @app.get("/health", tags=["system"])
    def health():
        with sessions() as db:
            db.execute(text("SELECT 1"))
        return {"status": "ok", "demo": settings.fixture_mode}

    @app.post("/auth/register", status_code=201, tags=["auth"])
    def register(body: Credentials):
        with sessions.begin() as db:
            user = User(email=str(body.email).casefold(), password_hash=passwords.hash(body.password))
            db.add(user)
            try:
                db.flush()
            except IntegrityError:
                raise HTTPException(409, "Email already registered") from None
            return {"id": user.id, "email": user.email}

    @app.post("/auth/login", tags=["auth"])
    def login(body: Credentials):
        with sessions() as db:
            user = db.scalar(select(User).where(User.email == str(body.email).casefold()))
            valid = passwords.verify(body.password, user.password_hash if user else dummy_hash)
            if not user or not valid:
                raise HTTPException(401, "Invalid email or password", headers={"WWW-Authenticate": "Bearer"})
            return {"access_token": token(user, settings), "token_type": "bearer",
                    "expires_in": settings.token_minutes * 60}

    @app.get("/users/me", tags=["auth"])
    def me(user=Depends(current_user)):
        return record(user, {"password_hash", "token_version"})

    @app.post("/auth/logout", status_code=204, tags=["auth"])
    def logout(user=Depends(current_user)):
        with sessions.begin() as db:
            db.execute(update(User).where(User.id == user.id).values(token_version=User.token_version + 1))
        return Response(status_code=204)

    @app.post("/auth/change-password", status_code=204, tags=["auth"])
    def change_password(body: PasswordChange, user=Depends(current_user)):
        with sessions.begin() as db:
            row = db.get(User, user.id, with_for_update=True)
            if not passwords.verify(body.current_password, row.password_hash):
                raise HTTPException(400, "Current password is incorrect")
            row.password_hash = passwords.hash(body.new_password)
            row.token_version += 1
        return Response(status_code=204)

    @app.get("/categories", tags=["search"])
    def categories(user=Depends(current_user)):
        return sorted(CATEGORIES)

    @app.get("/vendors", tags=["search"])
    def vendors(user=Depends(current_user)):
        configs = app.state.search.store.all()
        with sessions() as db:
            return [record(v) | {"enabled": configs[v.id].enabled} for v in db.scalars(select(Vendor))]

    @app.post("/searches", tags=["search"])
    def search(body: SearchInput, user=Depends(current_user)):
        return app.state.search.execute(body.query, body.category, body.vendors, user.id)[1]

    @app.get("/searches", tags=["history"])
    def history(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), user=Depends(current_user)):
        with sessions() as db:
            rows = db.scalars(select(SearchHistory).where(SearchHistory.user_id == user.id)
                              .order_by(SearchHistory.created_at.desc()).offset(offset).limit(limit))
            return [record(r, {"result"}) for r in rows]

    @app.get("/searches/{identity}", tags=["history"])
    def history_detail(identity: str, user=Depends(current_user)):
        with sessions() as db:
            return record(owned(db, SearchHistory, identity, user))

    @app.delete("/searches/{identity}", status_code=204, tags=["history"])
    def delete_history(identity: str, user=Depends(current_user)):
        with sessions.begin() as db:
            row = owned(db, SearchHistory, identity, user)
            if row.status == "running":
                raise HTTPException(409, "Wait for this search to finish before deleting it")
            db.delete(row)
        return Response(status_code=204)

    @app.get("/products", tags=["catalog"])
    def products(q: str = Query("", max_length=200), category: str | None = None,
                 limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), user=Depends(current_user)):
        statement = select(Product).order_by(Product.created_at.desc())
        if q:
            statement = statement.where(Product.name.icontains(q, autoescape=True))
        if category:
            if category not in CATEGORIES:
                raise HTTPException(422, "Unknown category")
            statement = statement.where(Product.category == category)
        with sessions() as db:
            return [record(p) for p in db.scalars(statement.offset(offset).limit(limit))]

    @app.get("/products/{identity}", tags=["catalog"])
    def product_detail(identity: str, user=Depends(current_user)):
        with sessions() as db:
            product = db.get(Product, identity)
            if not product:
                raise HTTPException(404, "Product not found")
            return record(product)

    @app.get("/products/{identity}/prices", tags=["catalog"])
    def prices(identity: str, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), user=Depends(current_user)):
        with sessions() as db:
            if not db.get(Product, identity):
                raise HTTPException(404, "Product not found")
            return [record(r, {"search_id"}) for r in db.scalars(select(PriceListing)
                    .where(PriceListing.product_id == identity).order_by(PriceListing.observed_at.desc())
                    .offset(offset).limit(limit))]

    @app.post("/alerts", status_code=201, tags=["alerts"])
    def create_alert(body: AlertInput, user=Depends(current_user)):
        with sessions.begin() as db:
            product = db.get(Product, body.product_id)
            if not product:
                raise HTTPException(404, "Product not found; search first")
            selected = app.state.search.vendors_for(product.category, body.vendors)
            if body.channel == "email" and not settings.smtp_host:
                raise HTTPException(503, "Email delivery is not configured")
            if body.channel == "push":
                if not settings.vapid_private_key:
                    raise HTTPException(503, "Browser push is not configured")
                if not db.scalar(select(PushSubscription).where(PushSubscription.user_id == user.id)):
                    raise HTTPException(422, "Register a browser push subscription first")
            row = PriceAlert(user_id=user.id, product_id=product.id, target_price=body.target_price,
                             channel=body.channel, vendors=selected)
            db.add(row)
            db.flush()
            return record(row)

    @app.get("/alerts", tags=["alerts"])
    def alerts(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), user=Depends(current_user)):
        with sessions() as db:
            return [record(r) for r in db.scalars(select(PriceAlert).where(PriceAlert.user_id == user.id)
                    .order_by(PriceAlert.created_at.desc()).offset(offset).limit(limit))]

    @app.get("/alerts/{identity}", tags=["alerts"])
    def get_alert(identity: str, user=Depends(current_user)):
        with sessions() as db:
            return record(owned(db, PriceAlert, identity, user))

    @app.patch("/alerts/{identity}", tags=["alerts"])
    def update_alert(identity: str, body: AlertPatch, user=Depends(current_user)):
        changes = body.model_dump(exclude_unset=True, exclude_none=True)
        if not changes:
            raise HTTPException(422, "Supply target_price or active")
        with sessions.begin() as db:
            row = owned(db, PriceAlert, identity, user)
            for key, value in changes.items():
                setattr(row, key, value)
            row.below_target = False
            row.next_check_at = now()
            db.execute(update(Notification).where(Notification.alert_id == row.id,
                       Notification.status == "pending").values(status="cancelled"))
            db.flush()
            return record(row)

    @app.delete("/alerts/{identity}", status_code=204, tags=["alerts"])
    def delete_alert(identity: str, user=Depends(current_user)):
        with sessions.begin() as db:
            db.delete(owned(db, PriceAlert, identity, user))
        return Response(status_code=204)

    @app.get("/notifications", tags=["notifications"])
    def notifications(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), user=Depends(current_user)):
        with sessions() as db:
            return [record(r) for r in db.scalars(select(Notification).where(Notification.user_id == user.id)
                    .order_by(Notification.created_at.desc()).offset(offset).limit(limit))]

    @app.get("/push/public-key", tags=["notifications"])
    def push_key(user=Depends(current_user)):
        if not settings.vapid_public_key:
            raise HTTPException(503, "Browser push is not configured")
        return {"publicKey": settings.vapid_public_key}

    @app.post("/push/subscriptions", status_code=201, tags=["notifications"])
    def subscribe(body: PushInput, user=Depends(current_user)):
        with sessions.begin() as db:
            row = db.scalar(select(PushSubscription).where(PushSubscription.endpoint == body.endpoint))
            if row and row.user_id != user.id:
                raise HTTPException(409, "Subscription is already registered")
            if not row:
                row = PushSubscription(user_id=user.id, **body.model_dump())
                db.add(row)
            else:
                row.keys = body.keys
            try:
                db.flush()
            except IntegrityError:
                raise HTTPException(409, "Subscription is already registered") from None
            return {"id": row.id}

    @app.delete("/push/subscriptions/{identity}", status_code=204, tags=["notifications"])
    def unsubscribe(identity: str, user=Depends(current_user)):
        with sessions.begin() as db:
            db.delete(owned(db, PushSubscription, identity, user))
        return Response(status_code=204)

    @app.get("/admin/jobs", tags=["admin"])
    def jobs(status: str | None = None, limit: int = Query(20, ge=1, le=100),
             offset: int = Query(0, ge=0), user=Depends(admin)):
        statement = select(ScrapingJob).order_by(ScrapingJob.created_at.desc())
        if status:
            statement = statement.where(ScrapingJob.status == status)
        with sessions() as db:
            return [record(r) for r in db.scalars(statement.offset(offset).limit(limit))]

    @app.get("/admin/jobs/{identity}", tags=["admin"])
    def job_detail(identity: str, user=Depends(admin)):
        with sessions() as db:
            row = db.get(ScrapingJob, identity)
            if not row:
                raise HTTPException(404, "Job not found")
            return record(row)

    @app.get("/admin/jobs/{identity}/logs", tags=["admin"])
    def logs(identity: str, user=Depends(admin)):
        return {"jobId": identity, "log": job_detail(identity, user)["detail"]["log"]}

    @app.post("/admin/jobs/{identity}/retry", tags=["admin"])
    def retry(identity: str, category: str = "groceries", user=Depends(admin)):
        if category not in CATEGORIES:
            raise HTTPException(422, "Unknown category")
        row = job_detail(identity, user)
        if row["status"] not in {"failed", "skipped"}:
            raise HTTPException(409, "Only failed or skipped jobs can be retried")
        return app.state.search.execute(row["query"], category, [row["vendor_id"]])[1]

    @app.get("/admin/sites", tags=["admin"])
    def sites(user=Depends(admin)):
        return [{k: v for k, v in cfg.to_dict().items() if k not in {"headers", "cookies"}}
                for cfg in app.state.search.store.all().values()]

    @app.patch("/admin/sites/{vendor_id}", tags=["admin"])
    def edit_site(vendor_id: str, body: ConfigPatch, user=Depends(admin)):
        if vendor_id not in app.state.search.store.all():
            raise HTTPException(404, "Vendor not found")
        changes = body.model_dump(mode="json", exclude_unset=True, exclude_none=True)
        if not changes:
            raise HTTPException(422, "Supply at least one configuration field")
        cfg = app.state.search.store.update(vendor_id, **changes).to_dict()
        return {k: v for k, v in cfg.items() if k not in {"headers", "cookies"}}

    return app
