"""Models of the access endpoints: setup, login, session, password change."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, StringConstraints

Username = Annotated[str, StringConstraints(min_length=1, max_length=64)]
NewPassword = Annotated[str, StringConstraints(min_length=12, max_length=1024)]
# Upper bounds only, so that hashing work cannot be inflated by a huge input.
SubmittedText = Annotated[str, StringConstraints(max_length=1024)]


class SetupStatus(BaseModel):
    setup_required: bool


class SetupRequest(BaseModel):
    token: SubmittedText
    username: Username
    password: NewPassword


class LoginRequest(BaseModel):
    username: SubmittedText
    password: SubmittedText


class SessionInfo(BaseModel):
    username: str
    expires_at: datetime


class PasswordChangeRequest(BaseModel):
    current_password: SubmittedText
    new_password: NewPassword


class HealthStatus(BaseModel):
    status: str
