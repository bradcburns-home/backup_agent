"""Environment-driven configuration for the backup agent."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings


class _SourceFlags(BaseSettings):
    """Per-source enable/disable flags."""

    source_mongodb: bool = True
    source_npm_sqlite: bool = True
    source_gateway_sqlite: bool = True
    source_chromadb: bool = True
    source_postgres: bool = False
    source_postgres_fax: bool = True
    source_postgres_agent_hub: bool = True
    source_postgres_meds: bool = True
    source_postgres_burns_config: bool = False  # flip true after the burns_config DB exists
    source_postgres_plaid: bool = True
    source_portainer: bool = True
    source_ha_config: bool = True
    source_mcp_identities: bool = True
    source_maintenance_tasks: bool = True
    source_mosquitto: bool = True
    source_loki_noise: bool = True
    source_letsencrypt: bool = True


class Settings(_SourceFlags):
    backup_interval_seconds: int = 3600
    backup_staging_dir: str = "/staging"

    restic_nfs_repository: str = "/mnt/nfs/backups/restic-repo"
    restic_gcs_repository: str = "gs:burns-backups-lab-backups:/restic-repo"
    restic_password_file: str = "/run/secrets/restic_password"
    google_application_credentials: str = "/run/secrets/gcs-credentials.json"

    nfs_retention_hourly: int = 24
    nfs_retention_daily: int = 7
    nfs_retention_weekly: int = 4
    nfs_retention_monthly: int = 12

    integrity_check_day: str = "Sunday"
    restore_test_hour: int = 4

    staging_retention_hours: int = 48

    status_db_path: str = "/data/backup_status.db"

    environment_name: str = "prod"
    log_level: str = "INFO"
    port: int = 8000

    @field_validator("backup_staging_dir")
    @classmethod
    def _ensure_staging_dir(cls, v: str) -> str:
        Path(v).mkdir(parents=True, exist_ok=True)
        return v

    model_config = {"env_file": ".env", "case_sensitive": False, "extra": "ignore"}


@lru_cache()
def get_settings() -> Settings:
    return Settings()
