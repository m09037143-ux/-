import uuid

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=200)


class MembershipOut(BaseModel):
    workspace_id: uuid.UUID
    workspace_name: str
    role: str


class MeResponse(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    memberships: list[MembershipOut]
