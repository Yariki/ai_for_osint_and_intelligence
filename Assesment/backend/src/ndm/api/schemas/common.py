"""Models shared by all endpoints: the one error shape."""

from pydantic import BaseModel


class ErrorDetail(BaseModel):
    """One problem, tied to the request field or list entry it concerns."""

    field: str | None = None
    code: str
    message: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] = []


class ErrorResponse(BaseModel):
    """The body of every response that is not a success."""

    error: ErrorBody
