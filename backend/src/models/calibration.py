from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.config.database import Base


class LocationCalibration(Base):
    """Append-only meter-bias calibration record for one rack and revision.

    Rows are never updated or deleted.  Arrival order carries no meaning: the
    winning record for a location is the one with the largest ``revision``.
    """

    __tablename__ = 'location_calibrations'
    __table_args__ = (UniqueConstraint('location', 'revision', name='uq_calibration_location_revision'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    location: Mapped[str] = mapped_column(String(40), index=True)
    bias_lower_pct: Mapped[float] = mapped_column()
    bias_upper_pct: Mapped[float] = mapped_column()
    revision: Mapped[int] = mapped_column(Integer)
    note: Mapped[str] = mapped_column(Text, default='')
    dataset_id: Mapped[int | None] = mapped_column(ForeignKey('datasets.id'), nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey('accounts.id'), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32))


class RunState(Base):
    """Frozen per-location slices kept so that a recalculation only has to
    rescore the locations whose calibration revision changed."""

    __tablename__ = 'run_states'

    run_id: Mapped[int] = mapped_column(ForeignKey('analysis_runs.id'), primary_key=True)
    state: Mapped[dict] = mapped_column(JSON)


class DatasetRelease(Base):
    """The single atomically switched sorting handoff for one dataset."""

    __tablename__ = 'dataset_releases'

    dataset_id: Mapped[int] = mapped_column(ForeignKey('datasets.id'), primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey('analysis_runs.id'))
    revisions: Mapped[dict] = mapped_column(JSON)
    switched_at: Mapped[str] = mapped_column(String(32))
