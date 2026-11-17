"""The database tables (SQLAlchemy 2.0 models).

Two groups, explained in docs/ARCHITECTURE.md ("Persistence"):

Simulation runs - one simulation_runs row per run, and everything that
happened in it hangs off that row (deleting a run deletes its history):

    simulation_runs ─┬─< stations ──< emergency_vehicles
                     ├─< emergencies  (sent vehicle: emergency_vehicles)
                     ├─< incidents    (closures and accidents; a closure
                     │                 made for an ambulance points at
                     │                 its emergency)
                     ├─< surges
                     ├─< fleet_spawns
                     ├─< run_events   (the ordered timeline; each event
                     │                 links to the row it is about)
                     └─< run_metrics  (a snapshot every N simulated s)

Optimiser runs - one optimiser_runs row per optimisation, its results as
real rows (one per ranked candidate, chosen station and baseline). It may
point at the simulation run whose closed roads it avoided; deleting that
run keeps the optimisation and just forgets the link.

Roads are not a table: the road network lives in the committed graph file
(10,980 road segments that never change). A road is stored the way the API
already names it, by its two junction ids and their coordinates, and each
run records a fingerprint of the graph file so ids are never read against
a different map.

Simulated times (sim_time, *_sim_time) are seconds since the run started;
wall-clock times are timestamps with time zone.
"""

import enum
import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# Predictable constraint names, so migrations can refer to them
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def pg_enum(enum_class, name):
    """A PostgreSQL enum type storing the Python enum's values."""

    return Enum(
        enum_class,
        name=name,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
    )


def utc_now_column(**options):
    return mapped_column(
        DateTime(timezone=True), server_default=func.now(), **options
    )


# ============================================================
# Enumerations
# ============================================================

class RunStatus(str, enum.Enum):
    ACTIVE = "active"            # live in memory now
    STOPPED = "stopped"          # stopped by a user or to make room
    REPLACED = "replaced"        # a new /simulation/start replaced it
    INTERRUPTED = "interrupted"  # the server stopped while it was live


class IncidentKind(str, enum.Enum):
    CLOSURE = "closure"          # road closed (/incident/block)
    ACCIDENT = "accident"        # road open but slow (/incident/accident)


class IncidentEndReason(str, enum.Enum):
    REOPENED = "reopened"        # closure lifted
    CLEARED = "cleared"          # accident cleared early
    EXPIRED = "expired"          # accident's time ran out
    REPLACED = "replaced"        # a new accident on the same road
    RUN_ENDED = "run_ended"      # still in place when the run ended


class EmergencyStatus(str, enum.Enum):
    DISPATCHED = "dispatched"    # a vehicle is on its way
    ARRIVED = "arrived"
    CANCELLED = "cancelled"      # nothing could be sent (see cancel_reason)
    RUN_ENDED = "run_ended"      # the run ended before the vehicle arrived


class SurgeDirection(str, enum.Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class EventType(str, enum.Enum):
    RUN_STARTED = "run_started"
    ROAD_CLOSED = "road_closed"
    ROAD_REOPENED = "road_reopened"
    ACCIDENT = "accident"
    ACCIDENT_CLEARED = "accident_cleared"
    STATION_ADDED = "station_added"
    VEHICLE_ADDED = "vehicle_added"
    EMERGENCY_DISPATCHED = "emergency_dispatched"
    EMERGENCY_ARRIVED = "emergency_arrived"
    EMERGENCY_CANCELLED = "emergency_cancelled"
    FLEET_ADDED = "fleet_added"
    SURGE_STARTED = "surge_started"
    REROUTING_CHANGED = "rerouting_changed"
    RUN_ENDED = "run_ended"


class OptimiserKind(str, enum.Enum):
    RANKING = "ranking"          # /optimiser/stations
    STATION_SET = "station_set"  # /optimiser/station-set


class CandidateSource(str, enum.Enum):
    DEFAULT = "default"          # the built-in spread over the map
    CUSTOM = "custom"            # given in the request


# ============================================================
# Simulation runs
# ============================================================

class SimulationRun(Base):
    __tablename__ = "simulation_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    status: Mapped[RunStatus] = mapped_column(
        pg_enum(RunStatus, "run_status"),
        server_default=RunStatus.ACTIVE.value,
    )

    start_node: Mapped[int] = mapped_column(BigInteger)
    destination_node: Mapped[int] = mapped_column(BigInteger)

    # SHA-256 of the road graph file the junction ids belong to
    graph_sha256: Mapped[str] = mapped_column(String(64))

    rerouting_enabled: Mapped[bool] = mapped_column(
        Boolean, server_default=text("true")
    )

    # How roads behaved (simulation/road_model.py). Runs saved before this
    # column existed used the original rules, hence the default.
    traffic_model: Mapped[str] = mapped_column(
        String(16), server_default=text("'simple'")
    )

    # Last recorded progress
    sim_time: Mapped[float] = mapped_column(Float, server_default=text("0"))
    vehicles_total: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    vehicles_finished: Mapped[int] = mapped_column(Integer, server_default=text("0"))

    # The ended run this one was restarted from (its saved setup), if any
    restarted_from: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("simulation_runs.id", ondelete="SET NULL"),
        index=True,
    )

    created_at: Mapped[datetime] = utc_now_column()
    updated_at: Mapped[datetime] = utc_now_column(onupdate=func.now())
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    stations: Mapped[List["Station"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True,
        order_by="Station.station_index",
    )
    emergency_vehicles: Mapped[List["EmergencyVehicle"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True,
        order_by="EmergencyVehicle.vehicle_index",
    )
    emergencies: Mapped[List["Emergency"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True,
        order_by="Emergency.id",
    )
    incidents: Mapped[List["Incident"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True,
        order_by="Incident.id",
    )
    surges: Mapped[List["Surge"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True,
        order_by="Surge.id",
    )
    fleet_spawns: Mapped[List["FleetSpawn"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True,
        order_by="FleetSpawn.id",
    )
    events: Mapped[List["RunEvent"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True,
        order_by="RunEvent.seq",
    )
    metrics: Mapped[List["RunMetric"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True,
        order_by="RunMetric.sim_time",
    )

    __table_args__ = (
        # A run has ended exactly when it is no longer active
        CheckConstraint(
            "(status = 'active') = (ended_at IS NULL)", name="ended_when_not_active"
        ),
        CheckConstraint("sim_time >= 0", name="sim_time_not_negative"),
        CheckConstraint(
            "traffic_model IN ('simple', 'osm')", name="known_traffic_model"
        ),
        CheckConstraint(
            "vehicles_finished >= 0 AND vehicles_finished <= vehicles_total",
            name="finished_within_total",
        ),
        Index("ix_simulation_runs_status_created", "status", "created_at"),
        Index("ix_simulation_runs_created_at", "created_at"),
    )


def run_id_column(index=True):
    """run_id, deleted with its run. index=False where a composite index
    or unique constraint already starts with run_id."""

    return mapped_column(
        UUID(as_uuid=True),
        ForeignKey("simulation_runs.id", ondelete="CASCADE"),
        index=index,
    )


class Station(Base):
    __tablename__ = "stations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[uuid.UUID] = run_id_column(index=False)

    # Position in the run's station list: the API's station_id
    station_index: Mapped[int] = mapped_column(Integer)

    station_type: Mapped[str] = mapped_column(String(32))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    created_sim_time: Mapped[float] = mapped_column(Float)

    run: Mapped[SimulationRun] = relationship(back_populates="stations")
    # Read-only: vehicles are added through SimulationRun.emergency_vehicles
    vehicles: Mapped[List["EmergencyVehicle"]] = relationship(
        viewonly=True, order_by="EmergencyVehicle.vehicle_index",
    )

    __table_args__ = (
        UniqueConstraint("run_id", "station_index"),
        # Lets other tables require "a station of the same run"
        UniqueConstraint("run_id", "id", name="uq_stations_run_id_id"),
        CheckConstraint("station_index >= 0", name="index_not_negative"),
    )


class EmergencyVehicle(Base):
    __tablename__ = "emergency_vehicles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    station_id: Mapped[int] = mapped_column(BigInteger, index=True)

    # Position in the run's vehicle list: the API's vehicle_id
    vehicle_index: Mapped[int] = mapped_column(Integer)

    vehicle_type: Mapped[str] = mapped_column(String(32))
    speed: Mapped[float] = mapped_column(Float)
    created_sim_time: Mapped[float] = mapped_column(Float)

    run: Mapped[SimulationRun] = relationship(back_populates="emergency_vehicles")
    # Read-only: station_id is set directly (it shares run_id with `run`)
    station: Mapped[Station] = relationship(viewonly=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id"], ["simulation_runs.id"], ondelete="CASCADE"
        ),
        # The station must belong to the same run
        ForeignKeyConstraint(
            ["run_id", "station_id"], ["stations.run_id", "stations.id"],
            ondelete="CASCADE",
            name="fk_emergency_vehicles_station_same_run",
        ),
        UniqueConstraint("run_id", "vehicle_index"),
        UniqueConstraint("run_id", "id", name="uq_emergency_vehicles_run_id_id"),
        CheckConstraint("speed > 0", name="speed_positive"),
        CheckConstraint("vehicle_index >= 0", name="index_not_negative"),
    )


class Emergency(Base):
    __tablename__ = "emergencies"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))

    emergency_type: Mapped[str] = mapped_column(String(32))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)

    status: Mapped[EmergencyStatus] = mapped_column(
        pg_enum(EmergencyStatus, "emergency_status")
    )
    # Why nothing could be sent: "no_vehicle", "no_route", or
    # "still_queued" (it waited for a vehicle until the run ended)
    cancel_reason: Mapped[Optional[str]] = mapped_column(String(32))

    # The vehicle sent (None if none was available)
    vehicle_id: Mapped[Optional[int]] = mapped_column(BigInteger, index=True)

    created_sim_time: Mapped[float] = mapped_column(Float)
    # When a vehicle was sent: later than created_sim_time if the emergency
    # waited in the queue (realistic emergency rules); None for emergencies
    # saved before this was recorded
    dispatched_sim_time: Mapped[Optional[float]] = mapped_column(Float)
    arrived_sim_time: Mapped[Optional[float]] = mapped_column(Float)
    response_seconds: Mapped[Optional[float]] = mapped_column(Float)

    # The route planned at dispatch
    route_distance_m: Mapped[Optional[float]] = mapped_column(Float)
    nodes_explored: Mapped[Optional[int]] = mapped_column(Integer)

    # Times the vehicle was rerouted on the way (final once arrived)
    reroutes: Mapped[int] = mapped_column(Integer, server_default=text("0"))

    created_at: Mapped[datetime] = utc_now_column()

    run: Mapped[SimulationRun] = relationship(back_populates="emergencies")
    # Read-only: vehicle_id is set directly (it shares run_id with `run`)
    vehicle: Mapped[Optional[EmergencyVehicle]] = relationship(viewonly=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id"], ["simulation_runs.id"], ondelete="CASCADE"
        ),
        # The vehicle sent must belong to the same run
        ForeignKeyConstraint(
            ["run_id", "vehicle_id"],
            ["emergency_vehicles.run_id", "emergency_vehicles.id"],
            ondelete="CASCADE",
            name="fk_emergencies_vehicle_same_run",
        ),
        CheckConstraint(
            "(status = 'arrived') = (arrived_sim_time IS NOT NULL "
            "AND response_seconds IS NOT NULL)",
            name="arrival_recorded_when_arrived",
        ),
        CheckConstraint(
            "(status = 'cancelled') = (cancel_reason IS NOT NULL)",
            name="reason_when_cancelled",
        ),
        CheckConstraint(
            "cancel_reason IS NULL OR cancel_reason IN ('no_vehicle', 'no_route', 'still_queued')",
            name="known_cancel_reason",
        ),
        CheckConstraint(
            "status IN ('cancelled') OR vehicle_id IS NOT NULL",
            name="vehicle_unless_cancelled",
        ),
        CheckConstraint(
            "response_seconds IS NULL OR response_seconds >= 0",
            name="response_not_negative",
        ),
        CheckConstraint("reroutes >= 0", name="reroutes_not_negative"),
        Index("ix_emergencies_run_status", "run_id", "status"),
    )


class Incident(Base):
    """A road closure or an accident, from when it started to when it ended."""

    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[uuid.UUID] = run_id_column(index=False)
    kind: Mapped[IncidentKind] = mapped_column(pg_enum(IncidentKind, "incident_kind"))

    # The road: its two junctions (in sorted coordinate order, like the
    # simulation's segment keys). Junction ids are None only if the
    # coordinates are not a junction of the run's graph.
    start_node: Mapped[Optional[int]] = mapped_column(BigInteger)
    end_node: Mapped[Optional[int]] = mapped_column(BigInteger)
    start_latitude: Mapped[float] = mapped_column(Float)
    start_longitude: Mapped[float] = mapped_column(Float)
    end_latitude: Mapped[float] = mapped_column(Float)
    end_longitude: Mapped[float] = mapped_column(Float)

    # Accidents only
    severity: Mapped[Optional[str]] = mapped_column(String(16))
    speed_factor: Mapped[Optional[float]] = mapped_column(Float)
    capacity_factor: Mapped[Optional[float]] = mapped_column(Float)
    planned_end_sim_time: Mapped[Optional[float]] = mapped_column(Float)

    started_sim_time: Mapped[float] = mapped_column(Float)
    ended_sim_time: Mapped[Optional[float]] = mapped_column(Float)
    end_reason: Mapped[Optional[IncidentEndReason]] = mapped_column(
        pg_enum(IncidentEndReason, "incident_end_reason")
    )

    # A closure made in front of an ambulance (/emergency/block-ahead)
    emergency_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("emergencies.id", ondelete="SET NULL"),
        index=True,
    )

    created_at: Mapped[datetime] = utc_now_column()

    run: Mapped[SimulationRun] = relationship(back_populates="incidents")
    emergency: Mapped[Optional[Emergency]] = relationship()

    __table_args__ = (
        # Accident details exactly when it is an accident
        CheckConstraint(
            "(kind = 'accident' AND severity IS NOT NULL AND speed_factor IS NOT NULL "
            "AND capacity_factor IS NOT NULL AND planned_end_sim_time IS NOT NULL) "
            "OR (kind = 'closure' AND severity IS NULL AND speed_factor IS NULL "
            "AND capacity_factor IS NULL AND planned_end_sim_time IS NULL)",
            name="accident_details_only_for_accidents",
        ),
        CheckConstraint(
            "severity IS NULL OR severity IN ('minor', 'moderate', 'severe')",
            name="known_severity",
        ),
        CheckConstraint(
            "(ended_sim_time IS NULL) = (end_reason IS NULL)",
            name="end_reason_when_ended",
        ),
        CheckConstraint(
            "ended_sim_time IS NULL OR ended_sim_time >= started_sim_time",
            name="ends_after_start",
        ),
        CheckConstraint(
            "kind = 'accident' OR end_reason IS NULL "
            "OR end_reason IN ('reopened', 'run_ended')",
            name="closure_end_reasons",
        ),
        CheckConstraint(
            "kind = 'closure' OR end_reason IS NULL OR end_reason <> 'reopened'",
            name="accident_end_reasons",
        ),
        # At most one open closure and one open accident per road
        Index(
            "uq_incidents_open_per_road",
            "run_id", "kind",
            "start_latitude", "start_longitude", "end_latitude", "end_longitude",
            unique=True,
            postgresql_where=text("ended_sim_time IS NULL"),
        ),
        Index("ix_incidents_run_started", "run_id", "started_sim_time"),
    )


class Surge(Base):
    __tablename__ = "surges"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[uuid.UUID] = run_id_column()

    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    radius_metres: Mapped[float] = mapped_column(Float)
    direction: Mapped[SurgeDirection] = mapped_column(
        pg_enum(SurgeDirection, "surge_direction")
    )
    vehicles_per_minute: Mapped[float] = mapped_column(Float)
    starts_sim_time: Mapped[float] = mapped_column(Float)
    ends_sim_time: Mapped[float] = mapped_column(Float)

    # Last recorded counts
    spawned: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    failed: Mapped[int] = mapped_column(Integer, server_default=text("0"))

    run: Mapped[SimulationRun] = relationship(back_populates="surges")

    __table_args__ = (
        CheckConstraint("radius_metres > 0", name="radius_positive"),
        CheckConstraint("vehicles_per_minute > 0", name="rate_positive"),
        CheckConstraint("ends_sim_time > starts_sim_time", name="ends_after_start"),
        CheckConstraint("spawned >= 0 AND failed >= 0", name="counts_not_negative"),
    )


class FleetSpawn(Base):
    __tablename__ = "fleet_spawns"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[uuid.UUID] = run_id_column()

    sim_time: Mapped[float] = mapped_column(Float)
    requested: Mapped[int] = mapped_column(Integer)
    spawned: Mapped[int] = mapped_column(Integer)
    failed: Mapped[int] = mapped_column(Integer)
    seed: Mapped[Optional[int]] = mapped_column(BigInteger)
    wall_seconds: Mapped[float] = mapped_column(Float)

    run: Mapped[SimulationRun] = relationship(back_populates="fleet_spawns")

    __table_args__ = (
        CheckConstraint("requested > 0", name="requested_positive"),
        CheckConstraint(
            "spawned >= 0 AND failed >= 0 AND spawned + failed <= requested",
            name="counts_add_up",
        ),
    )


class RunEvent(Base):
    """One entry in a run's timeline. The details live in the row it links
    to (an incident, emergency, station, ...), not in the event."""

    __tablename__ = "run_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("simulation_runs.id", ondelete="CASCADE")
    )

    # 1, 2, 3, ... within the run: the order things happened in
    seq: Mapped[int] = mapped_column(Integer)
    sim_time: Mapped[float] = mapped_column(Float)
    event_type: Mapped[EventType] = mapped_column(pg_enum(EventType, "event_type"))

    incident_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("incidents.id", ondelete="CASCADE")
    )
    emergency_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("emergencies.id", ondelete="CASCADE")
    )
    station_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("stations.id", ondelete="CASCADE")
    )
    vehicle_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("emergency_vehicles.id", ondelete="CASCADE")
    )
    surge_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("surges.id", ondelete="CASCADE")
    )
    fleet_spawn_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("fleet_spawns.id", ondelete="CASCADE")
    )

    created_at: Mapped[datetime] = utc_now_column()

    run: Mapped[SimulationRun] = relationship(back_populates="events")
    incident: Mapped[Optional[Incident]] = relationship()
    emergency: Mapped[Optional[Emergency]] = relationship()
    station: Mapped[Optional[Station]] = relationship()
    vehicle: Mapped[Optional[EmergencyVehicle]] = relationship()
    surge: Mapped[Optional[Surge]] = relationship()
    fleet_spawn: Mapped[Optional[FleetSpawn]] = relationship()

    __table_args__ = (
        UniqueConstraint("run_id", "seq"),
        CheckConstraint("seq > 0", name="seq_positive"),
        # Incident events point at their incident, emergency events at
        # their emergency, and so on
        CheckConstraint(
            "(event_type IN ('road_closed', 'road_reopened', 'accident', "
            "'accident_cleared')) = (incident_id IS NOT NULL)",
            name="incident_events_link_incident",
        ),
        CheckConstraint(
            "(event_type IN ('emergency_dispatched', 'emergency_arrived', "
            "'emergency_cancelled')) = (emergency_id IS NOT NULL)",
            name="emergency_events_link_emergency",
        ),
        CheckConstraint(
            "(event_type = 'station_added') = (station_id IS NOT NULL)",
            name="station_events_link_station",
        ),
        CheckConstraint(
            "(event_type = 'vehicle_added') = (vehicle_id IS NOT NULL)",
            name="vehicle_events_link_vehicle",
        ),
        CheckConstraint(
            "(event_type = 'surge_started') = (surge_id IS NOT NULL)",
            name="surge_events_link_surge",
        ),
        CheckConstraint(
            "(event_type = 'fleet_added') = (fleet_spawn_id IS NOT NULL)",
            name="fleet_events_link_fleet",
        ),
        # Deleting a run cascades through its incidents, emergencies, ...
        # into these events; without an index each cascaded row would scan
        # the whole events table. Partial: most events link to one thing.
        *(
            Index(
                f"ix_run_events_{column}",
                column,
                postgresql_where=text(f"{column} IS NOT NULL"),
            )
            for column in (
                "incident_id", "emergency_id", "station_id",
                "vehicle_id", "surge_id", "fleet_spawn_id",
            )
        ),
    )


class RunMetric(Base):
    """A snapshot of a run's traffic at one simulated time."""

    __tablename__ = "run_metrics"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("simulation_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    sim_time: Mapped[float] = mapped_column(Float, primary_key=True)

    vehicles_total: Mapped[int] = mapped_column(Integer)
    vehicles_finished: Mapped[int] = mapped_column(Integer)
    # Mean speed factor of vehicles still driving (None if none are)
    mean_speed_factor: Mapped[Optional[float]] = mapped_column(Float)
    congested_roads: Mapped[int] = mapped_column(Integer)
    closed_roads: Mapped[int] = mapped_column(Integer)
    active_accidents: Mapped[int] = mapped_column(Integer)
    reroutes_total: Mapped[int] = mapped_column(Integer)

    run: Mapped[SimulationRun] = relationship(back_populates="metrics")

    __table_args__ = (
        CheckConstraint(
            "vehicles_finished >= 0 AND vehicles_finished <= vehicles_total",
            name="finished_within_total",
        ),
        CheckConstraint(
            "mean_speed_factor IS NULL OR "
            "(mean_speed_factor >= 0 AND mean_speed_factor <= 1)",
            name="speed_factor_fraction",
        ),
    )


# ============================================================
# Optimiser runs
# ============================================================

class OptimiserRun(Base):
    __tablename__ = "optimiser_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    kind: Mapped[OptimiserKind] = mapped_column(pg_enum(OptimiserKind, "optimiser_kind"))

    # The run whose closed roads were avoided, if any
    simulation_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("simulation_runs.id", ondelete="SET NULL"),
        index=True,
    )

    # Settings
    scenarios: Mapped[int] = mapped_column(Integer)
    seed: Mapped[int] = mapped_column(BigInteger)
    target_seconds: Mapped[float] = mapped_column(Float)
    speed: Mapped[float] = mapped_column(Float)
    station_count: Mapped[Optional[int]] = mapped_column(Integer)
    candidate_source: Mapped[CandidateSource] = mapped_column(
        pg_enum(CandidateSource, "candidate_source")
    )
    candidates_considered: Mapped[int] = mapped_column(Integer)
    closed_roads: Mapped[int] = mapped_column(Integer)

    # Same map + settings + candidates + closed roads = same request_key,
    # and (the optimiser being deterministic) the same answer
    graph_sha256: Mapped[str] = mapped_column(String(64))
    request_key: Mapped[str] = mapped_column(String(64), index=True)

    explanation: Mapped[List[str]] = mapped_column(ARRAY(Text))

    # The chosen set's own figures (station sets only)
    set_average_seconds: Mapped[Optional[float]] = mapped_column(Float)
    set_worst_seconds: Mapped[Optional[float]] = mapped_column(Float)
    set_within_target_percent: Mapped[Optional[float]] = mapped_column(Float)
    set_unreachable: Mapped[Optional[int]] = mapped_column(Integer)
    set_ranking_seconds: Mapped[Optional[float]] = mapped_column(Float)

    compute_ms: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = utc_now_column()

    candidates: Mapped[List["OptimiserCandidate"]] = relationship(
        back_populates="optimiser_run", cascade="all, delete-orphan",
        passive_deletes=True, order_by="OptimiserCandidate.rank",
    )
    selected_stations: Mapped[List["OptimiserSelectedStation"]] = relationship(
        back_populates="optimiser_run", cascade="all, delete-orphan",
        passive_deletes=True, order_by="OptimiserSelectedStation.pick_order",
    )
    baselines: Mapped[List["OptimiserBaseline"]] = relationship(
        back_populates="optimiser_run", cascade="all, delete-orphan",
        passive_deletes=True, order_by="OptimiserBaseline.position",
    )

    __table_args__ = (
        CheckConstraint("scenarios BETWEEN 1 AND 5000", name="scenarios_range"),
        CheckConstraint("target_seconds > 0", name="target_positive"),
        CheckConstraint("speed > 0", name="speed_positive"),
        CheckConstraint(
            "(kind = 'station_set') = (station_count IS NOT NULL)",
            name="station_count_for_sets",
        ),
        CheckConstraint(
            "station_count IS NULL OR station_count BETWEEN 1 AND 10",
            name="station_count_range",
        ),
        CheckConstraint("closed_roads >= 0", name="closed_roads_not_negative"),
        Index("ix_optimiser_runs_kind_created", "kind", "created_at"),
    )


class OptimiserCandidate(Base):
    """One ranked candidate of a ranking optimisation."""

    __tablename__ = "optimiser_candidates"

    optimiser_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("optimiser_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    rank: Mapped[int] = mapped_column(Integer, primary_key=True)

    node: Mapped[int] = mapped_column(BigInteger)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    average_seconds: Mapped[Optional[float]] = mapped_column(Float)
    worst_seconds: Mapped[Optional[float]] = mapped_column(Float)
    within_target_percent: Mapped[float] = mapped_column(Float)
    unreachable: Mapped[int] = mapped_column(Integer)
    ranking_seconds: Mapped[float] = mapped_column(Float)

    optimiser_run: Mapped[OptimiserRun] = relationship(back_populates="candidates")

    __table_args__ = (
        CheckConstraint("rank >= 1", name="rank_positive"),
        CheckConstraint(
            "within_target_percent BETWEEN 0 AND 100", name="percent_range"
        ),
    )


class OptimiserSelectedStation(Base):
    """One station of a chosen set, in the order it adds the most."""

    __tablename__ = "optimiser_selected_stations"

    optimiser_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("optimiser_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    pick_order: Mapped[int] = mapped_column(Integer, primary_key=True)

    node: Mapped[int] = mapped_column(BigInteger)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    # The whole set so far, to show what each extra station adds
    average_seconds_after: Mapped[Optional[float]] = mapped_column(Float)
    within_target_percent_after: Mapped[float] = mapped_column(Float)

    optimiser_run: Mapped[OptimiserRun] = relationship(
        back_populates="selected_stations"
    )

    __table_args__ = (
        CheckConstraint("pick_order >= 1", name="order_positive"),
    )


class OptimiserBaseline(Base):
    """What a simpler choice of stations achieves, for comparison."""

    __tablename__ = "optimiser_baselines"

    optimiser_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("optimiser_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    name: Mapped[str] = mapped_column(String(32), primary_key=True)

    # Display order (best_single, top_individual, random)
    position: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(Text)
    stations: Mapped[int] = mapped_column(Integer)
    average_seconds: Mapped[Optional[float]] = mapped_column(Float)
    worst_seconds: Mapped[Optional[float]] = mapped_column(Float)
    within_target_percent: Mapped[Optional[float]] = mapped_column(Float)
    # A mean over random trials for "random", so not always whole
    unreachable: Mapped[float] = mapped_column(Float)
    ranking_seconds: Mapped[float] = mapped_column(Float)

    optimiser_run: Mapped[OptimiserRun] = relationship(back_populates="baselines")

    __table_args__ = (
        UniqueConstraint("optimiser_run_id", "position"),
    )


# ============================================================
# Scenario studies (Stage 4)
# ============================================================
#
#   scenario_studies ──< scenarios ──1 scenario_results
#                                   ├─< scenario_road_impacts
#                                   ├─< scenario_area_impacts
#                                   └─< scenario_timeseries
#
# A study is one batch job: scenarios run in the background and are
# compared with each other. A scenario's definition is kept as the
# validated JSON document it was submitted as (ScenarioSpec), with its
# fingerprint and trip signature as columns; everything measured is in
# typed columns and child tables.

class StudyStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"      # every scenario finished (some may have failed)
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"  # the server stopped while it was running


class ScenarioStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"            # the run raised an error (see error)
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


# Trip statuses in scenario_results.trip_status
TRIP_STATUS_CODES = {"finished": 0, "unfinished": 1, "unroutable": 2}


class ScenarioStudy(Base):
    __tablename__ = "scenario_studies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[StudyStatus] = mapped_column(
        pg_enum(StudyStatus, "study_status"), server_default=StudyStatus.QUEUED.value
    )

    # How the scenarios are ranked by default (a key of compare.METRICS)
    metric: Mapped[str] = mapped_column(String(64))

    scenario_count: Mapped[int] = mapped_column(Integer)
    completed_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    failed_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    workers: Mapped[int] = mapped_column(Integer)

    graph_sha256: Mapped[str] = mapped_column(String(64))
    engine_version: Mapped[int] = mapped_column(Integer)

    created_at: Mapped[datetime] = utc_now_column()
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    wall_seconds: Mapped[Optional[float]] = mapped_column(Float)

    scenarios: Mapped[List["Scenario"]] = relationship(
        back_populates="study", cascade="all, delete-orphan", passive_deletes=True,
        order_by="Scenario.position",
    )

    __table_args__ = (
        CheckConstraint("scenario_count BETWEEN 1 AND 5000", name="scenario_count_range"),
        CheckConstraint(
            "completed_count >= 0 AND failed_count >= 0 "
            "AND completed_count + failed_count <= scenario_count",
            name="counts_add_up",
        ),
        CheckConstraint("workers >= 1", name="workers_positive"),
        CheckConstraint(
            "(status IN ('queued', 'running')) = (finished_at IS NULL)",
            name="finished_when_done",
        ),
        Index("ix_scenario_studies_created_at", "created_at"),
    )


class Scenario(Base):
    __tablename__ = "scenarios"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    study_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scenario_studies.id", ondelete="CASCADE")
    )
    # Order within the study (0, 1, 2, ...)
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(120))
    is_baseline: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))

    # The validated ScenarioSpec, and what identifies it
    spec: Mapped[dict] = mapped_column(JSONB)
    seed: Mapped[int] = mapped_column(BigInteger)
    spec_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    trip_signature: Mapped[str] = mapped_column(String(16))

    status: Mapped[ScenarioStatus] = mapped_column(
        pg_enum(ScenarioStatus, "scenario_status"),
        server_default=ScenarioStatus.QUEUED.value,
    )
    error: Mapped[Optional[str]] = mapped_column(Text)

    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    study: Mapped[ScenarioStudy] = relationship(back_populates="scenarios")
    result: Mapped[Optional["ScenarioResult"]] = relationship(
        back_populates="scenario", cascade="all, delete-orphan", passive_deletes=True,
        uselist=False,
    )
    roads: Mapped[List["ScenarioRoadImpact"]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True,
        order_by="ScenarioRoadImpact.rank",
    )
    areas: Mapped[List["ScenarioAreaImpact"]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True,
        order_by="ScenarioAreaImpact.rank",
    )
    timeseries: Mapped[List["ScenarioTimeseries"]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True,
        order_by="ScenarioTimeseries.sim_time",
    )

    __table_args__ = (
        UniqueConstraint("study_id", "position"),
        CheckConstraint("position >= 0", name="position_not_negative"),
        CheckConstraint(
            "(status = 'failed') = (error IS NOT NULL)", name="error_when_failed"
        ),
        # At most one baseline per study
        Index(
            "uq_scenarios_one_baseline_per_study", "study_id",
            unique=True, postgresql_where=text("is_baseline"),
        ),
        Index("ix_scenarios_study_status", "study_id", "status"),
    )


class ScenarioResult(Base):
    """Everything measured in one scenario run (one row per scenario)."""

    __tablename__ = "scenario_results"

    scenario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scenarios.id", ondelete="CASCADE"),
        primary_key=True,
    )
    engine_version: Mapped[int] = mapped_column(Integer)

    # Trips
    trips_requested: Mapped[int] = mapped_column(Integer)
    trips_spawned: Mapped[int] = mapped_column(Integer)
    trips_unroutable: Mapped[int] = mapped_column(Integer)
    trips_finished: Mapped[int] = mapped_column(Integer)
    completion_percent: Mapped[Optional[float]] = mapped_column(Float)

    # Network-wide impact
    total_delay_vehicle_hours: Mapped[float] = mapped_column(Float)
    total_travel_vehicle_hours: Mapped[float] = mapped_column(Float)
    mean_delay_seconds: Mapped[Optional[float]] = mapped_column(Float)
    p95_delay_seconds: Mapped[Optional[float]] = mapped_column(Float)
    max_delay_seconds: Mapped[Optional[float]] = mapped_column(Float)
    mean_increase_seconds: Mapped[Optional[float]] = mapped_column(Float)
    p95_increase_seconds: Mapped[Optional[float]] = mapped_column(Float)
    max_increase_seconds: Mapped[Optional[float]] = mapped_column(Float)
    reroutes: Mapped[int] = mapped_column(Integer)
    simulated_seconds: Mapped[int] = mapped_column(Integer)
    hit_time_limit: Mapped[bool] = mapped_column(Boolean)

    # Emergencies (all None/0 when the scenario had none)
    emergencies_requested: Mapped[int] = mapped_column(Integer)
    emergencies_dispatched: Mapped[int] = mapped_column(Integer)
    emergencies_refused: Mapped[int] = mapped_column(Integer)
    emergencies_arrived: Mapped[int] = mapped_column(Integer)
    emergency_mean_response_seconds: Mapped[Optional[float]] = mapped_column(Float)
    emergency_worst_response_seconds: Mapped[Optional[float]] = mapped_column(Float)
    emergency_within_target_percent: Mapped[Optional[float]] = mapped_column(Float)
    # Queue and per-service figures of the emergencies (scenarios/runner.py
    # summarise_emergencies "detail"); None for results saved before them
    emergency_detail: Mapped[Optional[dict]] = mapped_column(JSONB)

    # Execution
    wall_seconds: Mapped[float] = mapped_column(Float)
    cpu_seconds: Mapped[float] = mapped_column(Float)
    peak_rss_mb: Mapped[float] = mapped_column(Float)
    vehicle_seconds: Mapped[int] = mapped_column(BigInteger)
    vehicle_seconds_per_second: Mapped[Optional[float]] = mapped_column(Float)

    # Per trip, in trip order (the same order in every scenario with the
    # same trip signature): ends, journey and free-flow time, delay, and
    # status (TRIP_STATUS_CODES). NULL elements for trips with no route.
    # Deferred (group "trips"): loaded only when asked for, so listing or
    # ranking a large study does not pull thousands of arrays
    trip_origin: Mapped[List[Optional[int]]] = mapped_column(
        ARRAY(BigInteger), deferred=True, deferred_group="trips")
    trip_destination: Mapped[List[Optional[int]]] = mapped_column(
        ARRAY(BigInteger), deferred=True, deferred_group="trips")
    trip_journey_seconds: Mapped[List[Optional[int]]] = mapped_column(
        ARRAY(Integer), deferred=True, deferred_group="trips")
    trip_free_flow_seconds: Mapped[List[Optional[float]]] = mapped_column(
        ARRAY(Float), deferred=True, deferred_group="trips")
    trip_delay_seconds: Mapped[List[Optional[float]]] = mapped_column(
        ARRAY(Float), deferred=True, deferred_group="trips")
    trip_status: Mapped[List[int]] = mapped_column(
        ARRAY(SmallInteger), deferred=True, deferred_group="trips")

    created_at: Mapped[datetime] = utc_now_column()

    scenario: Mapped[Scenario] = relationship(back_populates="result")

    __table_args__ = (
        CheckConstraint(
            "trips_spawned + trips_unroutable = trips_requested "
            "AND trips_finished <= trips_spawned",
            name="trips_add_up",
        ),
        CheckConstraint(
            "cardinality(trip_status) = trips_requested "
            "AND cardinality(trip_origin) = trips_requested "
            "AND cardinality(trip_destination) = trips_requested "
            "AND cardinality(trip_journey_seconds) = trips_requested "
            "AND cardinality(trip_free_flow_seconds) = trips_requested "
            "AND cardinality(trip_delay_seconds) = trips_requested",
            name="one_entry_per_trip",
        ),
        CheckConstraint(
            "total_delay_vehicle_hours >= 0 "
            "AND total_delay_vehicle_hours <= total_travel_vehicle_hours + 1e-9",
            name="delay_within_travel",
        ),
        CheckConstraint(
            "emergencies_dispatched + emergencies_refused <= emergencies_requested "
            "AND emergencies_arrived <= emergencies_dispatched",
            name="emergencies_add_up",
        ),
    )


class ScenarioRoadImpact(Base):
    """One of a scenario's most delayed roads."""

    __tablename__ = "scenario_road_impacts"

    scenario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scenarios.id", ondelete="CASCADE"),
        primary_key=True,
    )
    rank: Mapped[int] = mapped_column(Integer, primary_key=True)

    # None for the short "made-up" road a vehicle rerouted part-way along
    # a road drives until the next junction
    start_node: Mapped[Optional[int]] = mapped_column(BigInteger)
    end_node: Mapped[Optional[int]] = mapped_column(BigInteger)
    start_latitude: Mapped[float] = mapped_column(Float)
    start_longitude: Mapped[float] = mapped_column(Float)
    end_latitude: Mapped[float] = mapped_column(Float)
    end_longitude: Mapped[float] = mapped_column(Float)

    delay_vehicle_seconds: Mapped[float] = mapped_column(Float)
    vehicle_seconds: Mapped[int] = mapped_column(Integer)
    peak_vehicles: Mapped[int] = mapped_column(Integer)
    delay_share: Mapped[float] = mapped_column(Float)

    __table_args__ = (
        CheckConstraint("rank >= 1", name="rank_positive"),
        CheckConstraint("delay_vehicle_seconds > 0", name="delay_positive"),
    )


class ScenarioAreaImpact(Base):
    """One of a scenario's most delayed 500 m areas."""

    __tablename__ = "scenario_area_impacts"

    scenario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scenarios.id", ondelete="CASCADE"),
        primary_key=True,
    )
    rank: Mapped[int] = mapped_column(Integer, primary_key=True)

    cell_row: Mapped[int] = mapped_column(Integer)
    cell_col: Mapped[int] = mapped_column(Integer)
    south: Mapped[float] = mapped_column(Float)
    west: Mapped[float] = mapped_column(Float)
    north: Mapped[float] = mapped_column(Float)
    east: Mapped[float] = mapped_column(Float)
    delay_vehicle_seconds: Mapped[float] = mapped_column(Float)
    roads_affected: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        CheckConstraint("rank >= 1", name="rank_positive"),
        CheckConstraint("north > south AND east > west", name="cell_has_area"),
    )


class ScenarioTimeseries(Base):
    """A scenario's traffic over one simulated minute."""

    __tablename__ = "scenario_timeseries"

    scenario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scenarios.id", ondelete="CASCADE"),
        primary_key=True,
    )
    sim_time: Mapped[int] = mapped_column(Integer, primary_key=True)

    active_vehicles: Mapped[int] = mapped_column(Integer)
    finished_vehicles: Mapped[int] = mapped_column(Integer)
    delay_vehicle_seconds: Mapped[float] = mapped_column(Float)
    vehicle_seconds: Mapped[int] = mapped_column(Integer)
    mean_speed_factor: Mapped[Optional[float]] = mapped_column(Float)
    closed_roads: Mapped[int] = mapped_column(Integer)
    accidents: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        CheckConstraint("sim_time > 0", name="time_positive"),
        CheckConstraint(
            "mean_speed_factor IS NULL OR (mean_speed_factor >= 0 AND mean_speed_factor <= 1)",
            name="speed_factor_fraction",
        ),
    )


class StationEvaluation(Base):
    """A traffic-aware evaluation of the station optimiser's best answers
    (optimiser/evaluation.py): the scenario study that simulates them, the
    settings, the candidates with the optimiser's estimates, and which
    scenario position is which candidate, demand level and seed."""

    __tablename__ = "station_evaluations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    study_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scenario_studies.id", ondelete="CASCADE"),
        unique=True,
    )
    settings: Mapped[dict] = mapped_column(JSONB)
    candidates: Mapped[list] = mapped_column(JSONB)
    plan: Mapped[list] = mapped_column(JSONB)
    created_at: Mapped[datetime] = utc_now_column()
