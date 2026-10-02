from pydantic import BaseModel, Field, field_validator
from typing import Literal

class Credentials(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=10, max_length=128)
    name: str = Field(default='Explorateur', min_length=1, max_length=100)
    @field_validator('email')
    @classmethod
    def email_valid(cls, v):
        v = v.strip().lower()
        if '@' not in v or '.' not in v.split('@')[-1] or ' ' in v: raise ValueError('Adresse email invalide')
        return v

class Profile(BaseModel):
    search_type: str = Field(default='Premier emploi', max_length=100)
    domains: list[str] = Field(default_factory=list, max_length=25)
    skills: list[str] = Field(default_factory=list, max_length=100)
    technologies: list[str] = Field(default_factory=list, max_length=100)
    education_level: str = Field(default='', max_length=100)
    city: str = Field(default='', max_length=200)
    country: str = Field(default='France', max_length=100)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    radius_km: int = Field(default=50, ge=1, le=20000)
    remote_types: list[Literal['remote','hybrid','onsite']] = Field(default_factory=list)
    contracts: list[Literal['alternance','stage','CDI','CDD','graduate','freelance']] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list, max_length=30)
    seniority: str = Field(default='junior', max_length=60)
    preferences: str = Field(default='', max_length=2000)
    @field_validator('skills', 'technologies', 'domains', 'languages')
    @classmethod
    def bounded_strings(cls, values):
        if any(len(s)>100 for s in values): raise ValueError('Élément trop long')
        return list(dict.fromkeys(s.strip() for s in values if s.strip()))

class CompanyInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    website_url: str = Field(max_length=2000)
    career_url: str | None = Field(default=None, max_length=2000)
    ats_provider: Literal['generic','greenhouse','lever','lever_eu','ashby','smartrecruiters','recruitee','workday','teamtailor','successfactors','taleo'] = 'generic'
    ats_id: str | None = Field(default=None, pattern=r'^[a-zA-Z0-9_-]{1,150}$')
    industry: str = Field(default='Tech', max_length=100)
    country: str = Field(default='France', max_length=100)
    size: str | None = Field(default=None, max_length=100)
    cities: list[str] = Field(default_factory=list, max_length=50)

class ResumeInput(BaseModel):
    name: str = Field(default='Mon CV', min_length=1, max_length=200)
    text: str = Field(min_length=40, max_length=50000)

class TailorInput(BaseModel):
    resume_id: str
    job_id: str
    use_ai: bool = False

class SavedInput(BaseModel):
    stage: Literal['saved','applied','interview','offer','rejected'] = 'saved'
    note: str = Field(default='', max_length=3000)
