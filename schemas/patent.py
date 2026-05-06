from pydantic import BaseModel, Field


class Patent(BaseModel):
    application_number: str | None = None
    registration_number: str | None = None
    title: str | None = None
    abstract: str | None = None
    claims: list[str] = Field(default_factory=list)

