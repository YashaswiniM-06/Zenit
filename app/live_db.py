"""Private live-pilot persistence, independent of the synthetic demo database."""

from contextlib import contextmanager
from threading import RLock

from sqlalchemy import (
    CheckConstraint, Column, Float, ForeignKey, Index, Integer, String, create_engine,
    event, text,
)
from sqlalchemy.orm import DeclarativeBase, Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateSchema


class LiveBase(DeclarativeBase):
    pass


class LiveUser(LiveBase):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('rider', 'driver')"), {"schema": "zenit"})
    id = Column(String(40), primary_key=True)
    name = Column(String(80), nullable=False)
    email = Column(String(254), nullable=False, unique=True)
    password_hash = Column(String(256), nullable=False)
    role = Column(String(10), nullable=False)
    created_at = Column(Float, nullable=False)


class LiveSession(LiveBase):
    __tablename__ = "sessions"
    __table_args__ = {"schema": "zenit"}
    token_hash = Column(String(64), primary_key=True)
    user_id = Column(String(40), ForeignKey("zenit.users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(Float, nullable=False)
    expires_at = Column(Float, nullable=False, index=True)


class PasswordReset(LiveBase):
    __tablename__ = "password_resets"
    __table_args__ = {"schema": "zenit"}
    token_hash = Column(String(64), primary_key=True)
    user_id = Column(String(40), ForeignKey("zenit.users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(Float, nullable=False)
    expires_at = Column(Float, nullable=False)
    consumed_at = Column(Float, nullable=True)


class LiveDriver(LiveBase):
    __tablename__ = "drivers"
    __table_args__ = (
        CheckConstraint("vehicle_type IN ('bike', 'auto')"),
        CheckConstraint("status IN ('offline', 'available', 'busy')"),
        CheckConstraint("latitude BETWEEN -90 AND 90"),
        CheckConstraint("longitude BETWEEN -180 AND 180"),
        {"schema": "zenit"},
    )
    id = Column(String(40), primary_key=True)
    user_id = Column(String(40), ForeignKey("zenit.users.id", ondelete="CASCADE"), nullable=False, unique=True)
    vehicle_type = Column(String(10), nullable=False)
    vehicle_number = Column(String(20), nullable=False)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    accuracy = Column(Float, nullable=True)
    status = Column(String(12), nullable=False, default="offline")
    last_seen_at = Column(Float, nullable=True)


class LiveRide(LiveBase):
    __tablename__ = "rides"
    __table_args__ = (
        CheckConstraint("vehicle_type IN ('bike', 'auto')"),
        CheckConstraint("radius_km BETWEEN 0.25 AND 10"),
        CheckConstraint("pickup_lat BETWEEN -90 AND 90"),
        CheckConstraint("pickup_lng BETWEEN -180 AND 180"),
        CheckConstraint("dropoff_lat BETWEEN -90 AND 90"),
        CheckConstraint("dropoff_lng BETWEEN -180 AND 180"),
        CheckConstraint("status IN ('pending', 'assigned', 'in_progress', 'completed', 'cancelled', 'expired')"),
        CheckConstraint("status NOT IN ('assigned', 'in_progress') OR driver_id IS NOT NULL"),
        Index(
            "one_active_ride_per_rider", "rider_id", unique=True,
            postgresql_where=text("status IN ('pending', 'assigned', 'in_progress')"),
            sqlite_where=text("status IN ('pending', 'assigned', 'in_progress')"),
        ),
        Index(
            "one_active_ride_per_live_driver", "driver_id", unique=True,
            postgresql_where=text("status IN ('assigned', 'in_progress')"),
            sqlite_where=text("status IN ('assigned', 'in_progress')"),
        ),
        {"schema": "zenit"},
    )
    id = Column(String(40), primary_key=True)
    rider_id = Column(String(40), ForeignKey("zenit.users.id"), nullable=False, index=True)
    driver_id = Column(String(40), ForeignKey("zenit.drivers.id"), nullable=True, index=True)
    pickup_lat = Column(Float, nullable=False)
    pickup_lng = Column(Float, nullable=False)
    pickup_label = Column(String(160), nullable=False)
    dropoff_lat = Column(Float, nullable=False)
    dropoff_lng = Column(Float, nullable=False)
    dropoff_label = Column(String(160), nullable=False)
    vehicle_type = Column(String(10), nullable=False)
    radius_km = Column(Float, nullable=False)
    status = Column(String(16), nullable=False, default="pending", index=True)
    created_at = Column(Float, nullable=False)
    updated_at = Column(Float, nullable=False)
    expires_at = Column(Float, nullable=False, index=True)


class LiveEvent(LiveBase):
    __tablename__ = "events"
    __table_args__ = {"schema": "zenit"}
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(40), ForeignKey("zenit.users.id"), nullable=False, index=True)
    type = Column(String(40), nullable=False)
    message = Column(String(300), nullable=False)
    ride_id = Column(String(40), ForeignKey("zenit.rides.id"), nullable=True)
    created_at = Column(Float, nullable=False)


class LiveDatabase:
    """SQLite uses an immediate write lock; PostgreSQL uses row-level locks."""

    def __init__(self, url):
        self.url = url
        self.engine = None
        self.ready = False
        self._lock = RLock()

    @property
    def sqlite(self):
        return self.url.startswith("sqlite") if self.url else False

    def initialize(self):
        if not self.url:
            return
        url = self.url
        if url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url[len("postgres://"):]
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        options = {"pool_pre_ping": True}
        if self.sqlite:
            options["connect_args"] = {"check_same_thread": False, "timeout": 15}
            if url.endswith(":memory:") or url in ("sqlite://", "sqlite+pysqlite://"):
                options["poolclass"] = StaticPool
        else:
            options["connect_args"] = {"connect_timeout": 10, "sslmode": "require"}
            options["pool_size"] = 5
            options["max_overflow"] = 5
        self.engine = create_engine(url, **options)
        if self.sqlite:
            self.engine = self.engine.execution_options(schema_translate_map={"zenit": None})

            @event.listens_for(self.engine, "connect")
            def sqlite_pragmas(connection, record):
                cursor = connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA busy_timeout=15000")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.close()

        with self.engine.begin() as connection:
            if not self.sqlite:
                connection.execute(CreateSchema("zenit", if_not_exists=True))
            LiveBase.metadata.create_all(connection)
            if not self.sqlite:
                roles = connection.execute(text("SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated')")).scalars().all()
                for role in ["PUBLIC", *roles]:
                    # The role names come from the fixed whitelist above.
                    connection.execute(text(f"REVOKE ALL ON SCHEMA zenit FROM {role}"))
                    connection.execute(text(f"REVOKE ALL ON ALL TABLES IN SCHEMA zenit FROM {role}"))
                    connection.execute(text(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA zenit FROM {role}"))
                    connection.execute(text(f"ALTER DEFAULT PRIVILEGES IN SCHEMA zenit REVOKE ALL ON TABLES FROM {role}"))
        self.ready = True

    def close(self):
        if self.engine is not None:
            self.engine.dispose()
        self.ready = False

    @contextmanager
    def transaction(self):
        if not self.ready or self.engine is None:
            from app.errors import DispatchError
            raise DispatchError(503, "Live service is unavailable. Please try again shortly.")
        # An in-memory SQLite connection must never be used by concurrent
        # threads. The same lock is cheap for file-backed local test databases.
        if self.sqlite:
            with self._lock:
                with Session(self.engine, expire_on_commit=False) as session:
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                    try:
                        yield session
                        session.commit()
                    except BaseException:
                        session.rollback()
                        raise
        else:
            with Session(self.engine, expire_on_commit=False) as session:
                with session.begin():
                    yield session
