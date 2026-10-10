from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

DepartmentName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=100)]


DepartmentCode = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9_-]{1,16}$")]


class DepartmentIn(BaseModel):
    name: DepartmentName
    code: DepartmentCode | None = None


class DepartmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str | None = None
