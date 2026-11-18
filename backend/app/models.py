"""The shared, intentionally small business-profile contract."""

from pydantic import BaseModel, Field


CORE_FIELDS = ("businessName", "description", "address", "phone", "website", "timezone", "openingHours", "services", "paymentMethods")
CONTACT_FIELDS = ("source", "business_name", "voice_greeting_phrase", "full_name", "phone_number", "email")

class Profile(BaseModel):
    source: str = ""
    business_name: str = ""
    voice_greeting_phrase: str = ""
    full_name: str = ""
    phone_number: str = ""
    email: str = ""
    businessName: str = ""
    description: str = ""
    address: str = ""
    phone: str = ""
    website: str = ""
    timezone: str = ""
    openingHours: list[str] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)
    paymentMethods: list[str] = Field(default_factory=list)


class Location(Profile):
    sourceUrl: str = ""


class Extraction(BaseModel):
    navigationOfferings: list[dict[str, str]] = Field(default_factory=list)
    language: str = ""
    profile: Profile = Field(default_factory=Profile)
    provenance: dict[str, list[dict[str, str]]] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    locations: list[Location] = Field(default_factory=list)
    mapsCandidates: list[Location] = Field(default_factory=list)
    mapsStatus: str = "idle"
    htmlPagesFetched: int = 0
