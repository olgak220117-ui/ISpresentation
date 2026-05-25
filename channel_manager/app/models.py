import secrets
from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Date, ForeignKey, Text
from sqlalchemy.orm import relationship
from .database import Base


class Property(Base):
    __tablename__ = "properties"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    address = Column(String(500))
    description = Column(Text)
    # Token used in the public iCal URL so it's unguessable
    calendar_token = Column(String(64), unique=True, default=lambda: secrets.token_urlsafe(32))
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    platforms = relationship("PlatformConnection", back_populates="property", cascade="all, delete-orphan")
    bookings = relationship("Booking", back_populates="property", cascade="all, delete-orphan")


class PlatformConnection(Base):
    __tablename__ = "platform_connections"

    id = Column(Integer, primary_key=True, index=True)
    property_id = Column(Integer, ForeignKey("properties.id"), nullable=False)
    # airbnb | booking | agoda | other
    platform = Column(String(50), nullable=False)
    # URL to fetch iCal FROM this platform
    ical_export_url = Column(String(1000))
    enabled = Column(Boolean, default=True)
    last_synced_at = Column(DateTime)
    last_sync_error = Column(Text)

    property = relationship("Property", back_populates="platforms")


class Booking(Base):
    __tablename__ = "bookings"

    id = Column(Integer, primary_key=True, index=True)
    property_id = Column(Integer, ForeignKey("properties.id"), nullable=False)
    platform = Column(String(50), nullable=False)
    # UID from the iCal event — used to deduplicate
    external_uid = Column(String(500), nullable=False, index=True)
    guest_name = Column(String(200))
    check_in = Column(Date, nullable=False)
    check_out = Column(Date, nullable=False)
    status = Column(String(50), default="confirmed")
    raw_summary = Column(String(500))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    property = relationship("Property", back_populates="bookings")
