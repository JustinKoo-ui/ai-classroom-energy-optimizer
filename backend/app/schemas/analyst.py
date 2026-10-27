"""Bounded commentary contracts. Numerical metrics stay server-owned."""

from datetime import date as Date
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from app.domain.runtime import Period
from app.schemas.common import ContractModel

Paragraph = Annotated[str, Field(min_length=1, max_length=8000)]


class AnalystRequest(ContractModel):
    date: Date | None = None
    period: Period = "day"
    refresh: bool = False


class AnalystSections(ContractModel):
    executive_summary: Paragraph
    environmental_analysis: Paragraph
    device_runtime_analysis: Paragraph
    energy_analysis: Paragraph
    automation_observations: Paragraph
    recommendations: Annotated[list[Paragraph], Field(min_length=1, max_length=8)]
    data_quality_and_limitations: Paragraph


class ProviderReport(ContractModel):
    metrics_digest: str
    energy_basis: Literal["assumed_wattage_estimate"]
    sections: AnalystSections


class AnalystResponse(ContractModel):
    status: Literal["ready", "unavailable", "rate_limited"]
    message: str
    generated_at: datetime | None = None
    metrics_as_of: datetime | None = None
    summary_hash: str | None = None
    cached: bool = False
    provider: str | None = None
    model: str | None = None
    sections: AnalystSections | None = None
    mandatory_disclosures: list[str] = Field(default_factory=list)
    retry_after_seconds: int | None = None
