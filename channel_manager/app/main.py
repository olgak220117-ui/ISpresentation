import os
import logging
from contextlib import asynccontextmanager
from datetime import date
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .database import Base, engine, get_db
from .models import Booking, PlatformConnection, Property
from .scheduler import start_scheduler, stop_scheduler
from .sync import generate_master_ical, sync_all, sync_property

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="IS Channel Manager", version="1.0.0", lifespan=lifespan)


# ─── Pydantic schemas ────────────────────────────────────────────────────────

class PropertyCreate(BaseModel):
    name: str
    address: Optional[str] = None
    description: Optional[str] = None


class PropertyUpdate(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    description: Optional[str] = None
    active: Optional[bool] = None


class PlatformCreate(BaseModel):
    platform: str  # airbnb | booking | agoda | other
    ical_export_url: str
    enabled: bool = True


class PlatformUpdate(BaseModel):
    ical_export_url: Optional[str] = None
    enabled: Optional[bool] = None


# ─── Dashboard ───────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    properties = db.query(Property).filter_by(active=True).order_by(Property.name).all()
    props_data = []
    for prop in properties:
        confirmed = (
            db.query(Booking)
            .filter_by(property_id=prop.id, status="confirmed")
            .count()
        )
        upcoming = (
            db.query(Booking)
            .filter(
                Booking.property_id == prop.id,
                Booking.status == "confirmed",
                Booking.check_in >= date.today(),
            )
            .count()
        )
        calendar_url = f"{BASE_URL}/calendar/{prop.calendar_token}"
        props_data.append({
            "id": prop.id,
            "name": prop.name,
            "address": prop.address or "",
            "platforms": prop.platforms,
            "confirmed_total": confirmed,
            "upcoming": upcoming,
            "calendar_url": calendar_url,
        })
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "properties": props_data, "base_url": BASE_URL},
    )


# ─── Properties ──────────────────────────────────────────────────────────────

@app.get("/api/properties")
def list_properties(db: Session = Depends(get_db)):
    props = db.query(Property).order_by(Property.name).all()
    return [
        {
            "id": p.id,
            "name": p.name,
            "address": p.address,
            "description": p.description,
            "active": p.active,
            "calendar_url": f"{BASE_URL}/calendar/{p.calendar_token}",
            "platforms": [
                {
                    "id": c.id,
                    "platform": c.platform,
                    "ical_export_url": c.ical_export_url,
                    "enabled": c.enabled,
                    "last_synced_at": c.last_synced_at,
                    "last_sync_error": c.last_sync_error,
                }
                for c in p.platforms
            ],
        }
        for p in props
    ]


@app.post("/api/properties", status_code=201)
def create_property(body: PropertyCreate, db: Session = Depends(get_db)):
    prop = Property(name=body.name, address=body.address, description=body.description)
    db.add(prop)
    db.commit()
    db.refresh(prop)
    return {"id": prop.id, "name": prop.name, "calendar_url": f"{BASE_URL}/calendar/{prop.calendar_token}"}


@app.get("/api/properties/{property_id}")
def get_property(property_id: int, db: Session = Depends(get_db)):
    prop = db.query(Property).filter_by(id=property_id).first()
    if not prop:
        raise HTTPException(404, "Property not found")
    bookings = (
        db.query(Booking)
        .filter_by(property_id=property_id, status="confirmed")
        .order_by(Booking.check_in)
        .all()
    )
    return {
        "id": prop.id,
        "name": prop.name,
        "address": prop.address,
        "description": prop.description,
        "active": prop.active,
        "calendar_url": f"{BASE_URL}/calendar/{prop.calendar_token}",
        "platforms": [
            {
                "id": c.id,
                "platform": c.platform,
                "ical_export_url": c.ical_export_url,
                "enabled": c.enabled,
                "last_synced_at": c.last_synced_at,
                "last_sync_error": c.last_sync_error,
            }
            for c in prop.platforms
        ],
        "bookings": [
            {
                "id": b.id,
                "platform": b.platform,
                "check_in": b.check_in.isoformat(),
                "check_out": b.check_out.isoformat(),
                "raw_summary": b.raw_summary,
            }
            for b in bookings
        ],
    }


@app.put("/api/properties/{property_id}")
def update_property(property_id: int, body: PropertyUpdate, db: Session = Depends(get_db)):
    prop = db.query(Property).filter_by(id=property_id).first()
    if not prop:
        raise HTTPException(404, "Property not found")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(prop, field, value)
    db.commit()
    return {"ok": True}


@app.delete("/api/properties/{property_id}")
def delete_property(property_id: int, db: Session = Depends(get_db)):
    prop = db.query(Property).filter_by(id=property_id).first()
    if not prop:
        raise HTTPException(404, "Property not found")
    db.delete(prop)
    db.commit()
    return {"ok": True}


# ─── Platform connections ─────────────────────────────────────────────────────

VALID_PLATFORMS = {"airbnb", "booking", "agoda", "other"}


@app.post("/api/properties/{property_id}/platforms", status_code=201)
def add_platform(property_id: int, body: PlatformCreate, db: Session = Depends(get_db)):
    if not db.query(Property).filter_by(id=property_id).first():
        raise HTTPException(404, "Property not found")
    if body.platform not in VALID_PLATFORMS:
        raise HTTPException(400, f"platform must be one of {sorted(VALID_PLATFORMS)}")
    conn = PlatformConnection(
        property_id=property_id,
        platform=body.platform,
        ical_export_url=body.ical_export_url,
        enabled=body.enabled,
    )
    db.add(conn)
    db.commit()
    db.refresh(conn)
    return {"id": conn.id, "platform": conn.platform}


@app.put("/api/platforms/{connection_id}")
def update_platform(connection_id: int, body: PlatformUpdate, db: Session = Depends(get_db)):
    conn = db.query(PlatformConnection).filter_by(id=connection_id).first()
    if not conn:
        raise HTTPException(404, "Platform connection not found")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(conn, field, value)
    db.commit()
    return {"ok": True}


@app.delete("/api/platforms/{connection_id}")
def delete_platform(connection_id: int, db: Session = Depends(get_db)):
    conn = db.query(PlatformConnection).filter_by(id=connection_id).first()
    if not conn:
        raise HTTPException(404, "Platform connection not found")
    db.delete(conn)
    db.commit()
    return {"ok": True}


# ─── Sync ─────────────────────────────────────────────────────────────────────

@app.post("/api/sync/all")
def manual_sync_all(db: Session = Depends(get_db)):
    results = sync_all(db)
    return {"synced_properties": len(results), "results": results}


@app.post("/api/sync/{property_id}")
def manual_sync_property(property_id: int, db: Session = Depends(get_db)):
    if not db.query(Property).filter_by(id=property_id).first():
        raise HTTPException(404, "Property not found")
    results = sync_property(db, property_id)
    return {"property_id": property_id, "results": results}


# ─── Master iCal feed (public URL) ───────────────────────────────────────────

@app.get("/calendar/{token}")
def master_calendar(token: str, db: Session = Depends(get_db)):
    prop = db.query(Property).filter_by(calendar_token=token).first()
    if not prop:
        raise HTTPException(404, "Calendar not found")
    ical_bytes = generate_master_ical(db, prop.id)
    return Response(
        content=ical_bytes,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{prop.name}.ics"'},
    )


# ─── Bookings ─────────────────────────────────────────────────────────────────

@app.get("/api/bookings")
def list_bookings(
    property_id: Optional[int] = None,
    platform: Optional[str] = None,
    upcoming_only: bool = False,
    db: Session = Depends(get_db),
):
    q = db.query(Booking).filter_by(status="confirmed")
    if property_id:
        q = q.filter(Booking.property_id == property_id)
    if platform:
        q = q.filter(Booking.platform == platform)
    if upcoming_only:
        q = q.filter(Booking.check_in >= date.today())
    bookings = q.order_by(Booking.check_in).all()
    return [
        {
            "id": b.id,
            "property_id": b.property_id,
            "platform": b.platform,
            "check_in": b.check_in.isoformat(),
            "check_out": b.check_out.isoformat(),
            "raw_summary": b.raw_summary,
            "updated_at": b.updated_at,
        }
        for b in bookings
    ]
