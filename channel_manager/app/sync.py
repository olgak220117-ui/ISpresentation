"""
iCal synchronisation engine.

Flow:
  1. Fetch raw iCal bytes from each platform connection URL.
  2. Parse VEVENT entries → upsert into Booking table.
  3. Mark stale bookings (UIDs no longer in feed) as cancelled.
  4. Expose a merged master iCal per property — platforms import this URL to
     block their own calendars automatically.
"""

import logging
from datetime import date, datetime, timezone
from typing import Optional

import requests
from icalendar import Calendar, Event, vText
from sqlalchemy.orm import Session

from .models import Booking, PlatformConnection, Property

logger = logging.getLogger(__name__)

PLATFORM_DISPLAY = {
    "airbnb": "Airbnb",
    "booking": "Booking.com",
    "agoda": "Agoda",
    "other": "Other",
}

REQUEST_TIMEOUT = 15  # seconds


def _to_date(val) -> Optional[date]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    return None


def fetch_ical_events(url: str) -> list[dict]:
    """Download iCal from *url* and return a list of event dicts."""
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": "ChannelManager/1.0"})
        resp.raise_for_status()
    except Exception as exc:
        raise RuntimeError(f"Failed to fetch iCal: {exc}") from exc

    cal = Calendar.from_ical(resp.content)
    events = []
    for component in cal.walk():
        if component.name != "VEVENT":
            continue
        uid = str(component.get("UID", ""))
        summary = str(component.get("SUMMARY", ""))
        dtstart = _to_date(getattr(component.get("DTSTART"), "dt", None))
        dtend = _to_date(getattr(component.get("DTEND"), "dt", None))
        if not uid or not dtstart or not dtend:
            continue
        events.append({"uid": uid, "summary": summary, "check_in": dtstart, "check_out": dtend})
    return events


def sync_platform(db: Session, connection: PlatformConnection) -> int:
    """Sync one platform connection. Returns number of bookings upserted."""
    if not connection.ical_export_url:
        return 0

    try:
        events = fetch_ical_events(connection.ical_export_url)
    except RuntimeError as exc:
        connection.last_sync_error = str(exc)
        connection.last_synced_at = datetime.utcnow()
        db.commit()
        logger.warning("Sync error [property=%s platform=%s]: %s", connection.property_id, connection.platform, exc)
        return 0

    seen_uids: set[str] = set()
    upserted = 0

    for ev in events:
        uid = ev["uid"]
        seen_uids.add(uid)
        booking = (
            db.query(Booking)
            .filter_by(property_id=connection.property_id, external_uid=uid)
            .first()
        )
        if booking is None:
            booking = Booking(
                property_id=connection.property_id,
                platform=connection.platform,
                external_uid=uid,
            )
            db.add(booking)

        booking.check_in = ev["check_in"]
        booking.check_out = ev["check_out"]
        booking.raw_summary = ev["summary"][:500] if ev["summary"] else None
        booking.status = "confirmed"
        booking.updated_at = datetime.utcnow()
        upserted += 1

    # Cancel bookings that disappeared from the feed
    db.query(Booking).filter(
        Booking.property_id == connection.property_id,
        Booking.platform == connection.platform,
        Booking.status == "confirmed",
        Booking.external_uid.notin_(seen_uids),
    ).update({"status": "cancelled", "updated_at": datetime.utcnow()})

    connection.last_synced_at = datetime.utcnow()
    connection.last_sync_error = None
    db.commit()
    return upserted


def sync_property(db: Session, property_id: int) -> dict:
    """Sync all enabled platforms for a property."""
    connections = (
        db.query(PlatformConnection)
        .filter_by(property_id=property_id, enabled=True)
        .all()
    )
    results = {}
    for conn in connections:
        results[conn.platform] = sync_platform(db, conn)
    return results


def sync_all(db: Session) -> dict:
    """Sync every active property."""
    properties = db.query(Property).filter_by(active=True).all()
    all_results = {}
    for prop in properties:
        all_results[prop.id] = sync_property(db, prop.id)
    return all_results


def generate_master_ical(db: Session, property_id: int) -> bytes:
    """
    Produce a master iCal that includes all confirmed bookings for a property.
    Platforms should import this URL to block out occupied dates.
    """
    prop = db.query(Property).filter_by(id=property_id).first()
    if not prop:
        raise ValueError(f"Property {property_id} not found")

    cal = Calendar()
    cal.add("prodid", "-//ChannelManager//IS//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", vText(prop.name))
    cal.add("x-wr-caldesc", vText(f"Master calendar – {prop.name}"))

    bookings = (
        db.query(Booking)
        .filter_by(property_id=property_id, status="confirmed")
        .all()
    )

    for b in bookings:
        event = Event()
        event.add("uid", f"{b.external_uid}@channelmanager")
        event.add("summary", vText(f"[{PLATFORM_DISPLAY.get(b.platform, b.platform)}] {b.raw_summary or 'Reserved'}"))
        event.add("dtstart", b.check_in)
        event.add("dtend", b.check_out)
        event.add("dtstamp", datetime.now(timezone.utc))
        event.add("status", "CONFIRMED")
        cal.add_component(event)

    return cal.to_ical()
