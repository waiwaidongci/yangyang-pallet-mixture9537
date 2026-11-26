from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, constr

from src.domain.calibration import BIAS_LIMIT, MAX_REVISION, MIN_REVISION

Location = Annotated[str, constr(strip_whitespace=True, min_length=1, max_length=40)]


class CalibrationRecord(BaseModel):
    model_config = ConfigDict(extra='forbid')
    location: Location
    bias_lower_pct: float = Field(ge=-BIAS_LIMIT, le=BIAS_LIMIT)
    bias_upper_pct: float = Field(ge=-BIAS_LIMIT, le=BIAS_LIMIT)
    revision: int = Field(ge=MIN_REVISION, le=MAX_REVISION)


class CalibrationBatch(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    records: list[CalibrationRecord] = Field(min_length=1, max_length=5000)
    note: str = Field(default='', max_length=1000)


class CalibrationLocation(BaseModel):
    location: str
    bias_lower_pct: float
    bias_upper_pct: float
    revision: int


class CalibrationState(BaseModel):
    locations: list[CalibrationLocation]
    versions: dict[str, int]
