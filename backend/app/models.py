import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Text, DateTime, JSON, ForeignKey, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base

def uid(): return str(uuid.uuid4())
def now(): return datetime.now(timezone.utc).replace(tzinfo=None)

class User(Base):
    __tablename__ = 'users'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(String(100))
    is_admin: Mapped[bool] = mapped_column(default=False)
    profile: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class AuthSession(Base):
    __tablename__ = 'auth_sessions'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    csrf: Mapped[str] = mapped_column(String(64))
    expires: Mapped[datetime] = mapped_column(DateTime, index=True)

class Company(Base):
    __tablename__ = 'companies'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(200))
    domain: Mapped[str] = mapped_column(String(254), unique=True)
    logo: Mapped[str | None] = mapped_column(Text)
    industry: Mapped[str] = mapped_column(String(100), default='Tech')
    size: Mapped[str | None] = mapped_column(String(100))
    country: Mapped[str | None] = mapped_column(String(100))
    cities: Mapped[list] = mapped_column(JSON, default=list)
    website_url: Mapped[str] = mapped_column(Text)
    career_url: Mapped[str | None] = mapped_column(Text)
    ats_provider: Mapped[str] = mapped_column(String(50), default='generic')
    ats_id: Mapped[str | None] = mapped_column(String(200))
    last_analysis: Mapped[datetime | None] = mapped_column(DateTime)
    last_crawl: Mapped[datetime | None] = mapped_column(DateTime)
    last_success: Mapped[datetime | None] = mapped_column(DateTime)
    crawler_status: Mapped[str] = mapped_column(String(50), default='idle')
    active_jobs: Mapped[int] = mapped_column(default=0)
    enabled: Mapped[bool] = mapped_column(default=True)
    is_demo: Mapped[bool] = mapped_column(default=False)

class Job(Base):
    __tablename__ = 'jobs'
    __table_args__ = (UniqueConstraint('company_id', 'dedupe_key'), Index('ix_job_status_domain', 'status', 'domain'))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    company_id: Mapped[str] = mapped_column(ForeignKey('companies.id'), index=True)
    source: Mapped[str] = mapped_column(String(50))
    source_url: Mapped[str] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(String(250))
    dedupe_key: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(500))
    normalized_title: Mapped[str] = mapped_column(String(500), index=True)
    description: Mapped[str] = mapped_column(Text)
    short_description: Mapped[str] = mapped_column(Text, default='')
    contract_type: Mapped[str] = mapped_column(String(60), default='unknown')
    employment_type: Mapped[str | None] = mapped_column(String(60))
    apprenticeship: Mapped[bool] = mapped_column(default=False)
    internship: Mapped[bool] = mapped_column(default=False)
    seniority: Mapped[str | None] = mapped_column(String(60))
    education_level: Mapped[str | None] = mapped_column(String(100))
    location: Mapped[str | None] = mapped_column(String(500))
    city: Mapped[str | None] = mapped_column(String(200))
    region: Mapped[str | None] = mapped_column(String(200))
    country: Mapped[str | None] = mapped_column(String(100))
    latitude: Mapped[float | None]
    longitude: Mapped[float | None]
    remote_type: Mapped[str] = mapped_column(String(50), default='unknown')
    salary_min: Mapped[float | None]
    salary_max: Mapped[float | None]
    salary_currency: Mapped[str | None] = mapped_column(String(10))
    skills: Mapped[list] = mapped_column(JSON, default=list)
    technologies: Mapped[list] = mapped_column(JSON, default=list)
    languages: Mapped[list] = mapped_column(JSON, default=list)
    domain: Mapped[str] = mapped_column(String(100), default='Autre')
    subdomain: Mapped[str | None] = mapped_column(String(100))
    responsibilities: Mapped[list] = mapped_column(JSON, default=list)
    requirements: Mapped[list] = mapped_column(JSON, default=list)
    nice_to_have: Mapped[list] = mapped_column(JSON, default=list)
    publication_date: Mapped[datetime | None] = mapped_column(DateTime)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=now)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=now)
    expiration_date: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(50), default='active', index=True)
    missing_count: Mapped[int] = mapped_column(default=0)
    is_demo: Mapped[bool] = mapped_column(default=False)

class JobSource(Base):
    __tablename__ = 'job_sources'
    __table_args__ = (UniqueConstraint('company_id', 'identity'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id'), index=True)
    company_id: Mapped[str] = mapped_column(ForeignKey('companies.id'))
    identity: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(60))
    url: Mapped[str] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(String(250))
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=now)

class CrawlRun(Base):
    __tablename__ = 'crawl_runs'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    company_id: Mapped[str] = mapped_column(ForeignKey('companies.id'), index=True)
    status: Mapped[str] = mapped_column(String(40), default='queued')
    started_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    count: Mapped[int] = mapped_column(default=0)
    complete: Mapped[bool] = mapped_column(default=False)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(default=0)

class HttpCache(Base):
    __tablename__ = 'http_cache'
    url: Mapped[str] = mapped_column(String(2000), primary_key=True)
    etag: Mapped[str | None] = mapped_column(Text)
    modified: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    updated: Mapped[datetime] = mapped_column(DateTime, default=now)

class Resume(Base):
    __tablename__ = 'resumes'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    name: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class TailoredResume(Base):
    __tablename__ = 'tailored_resumes'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    resume_id: Mapped[str] = mapped_column(ForeignKey('resumes.id', ondelete='CASCADE'))
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id'))
    text: Mapped[str] = mapped_column(Text)
    explanation: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

class SavedJob(Base):
    __tablename__ = 'saved_jobs'
    __table_args__ = (UniqueConstraint('user_id', 'job_id'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id'))
    stage: Mapped[str] = mapped_column(String(30), default='saved')
    note: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
