from pydantic import BaseModel, ConfigDict, Field

from src.constants.workflow import RunStatus


class RunCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    label: str = Field(min_length=2, max_length=140)
    dataset_id: int = Field(gt=0)
    settings: dict[str, float] = Field(default_factory=dict, max_length=20)
    # Ask the worker to rescore only the locations whose calibration moved.
    recompute: bool = False
    parent_run_id: int | None = Field(default=None, gt=0)


class RunInfo(BaseModel):
    id: int
    label: str
    dataset_id: int
    created_at: str
    status: RunStatus
    settings: dict
    decision: str | None
    parent_run_id: int | None = None
    calibration_versions: dict = Field(default_factory=dict)
    current: bool | None = None
    is_current: bool | None = None
    released: bool | None = None
