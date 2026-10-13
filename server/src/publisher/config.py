import os
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / "infra/config.env", extra="ignore"
    )
    app_mode: Literal["api", "viewer"] = "api"
    auth_mode: Literal["google", "dev"] = "google"
    storage_backend: Literal["local", "gcp"] = "gcp"
    local_data: Path = ROOT / "server/.localdata"
    gcp_project_id: str = ""
    gcp_project_number: str = ""
    region: str = "europe-west1"
    firestore_database: str = "artifact-publisher"
    bucket: str = ""
    api_service: str = "artifact-publisher-api"
    viewer_service: str = "share"
    api_url: str = ""
    viewer_url: str = ""
    org_display_name: str = "Example organization"
    plugin_name: str = "artifact-publisher"
    plugin_display_name: str = "Artifact Publisher"
    admin_contact: str = "your administrator"
    allowed_email_domains: str = ""
    allowed_emails: str = ""
    admin_emails: str = ""
    default_access_mode: Literal["link", "password"] = "link"
    max_html_bytes: int = 2_000_000
    git_sha: str = "local"
    cookie_signing_key: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    jwt_signing_key: str = ""
    storage_encryption_key: str = ""
    domain_strategy: Literal["load-balancer", "domain-mapping"] = "load-balancer"
    dns_provider_default: str = "squarespace"
    suggested_subdomain: str = "share"
    ar_repo: str = "artifact-publisher"
    umami_enabled: bool = False
    umami_url: str = ""
    umami_api_url: str = ""
    umami_script_url: str = ""
    umami_team_id: str = ""
    umami_credentials: str = Field(default="", repr=False)

    @model_validator(mode="after")
    def validate_runtime(self):
        if self.auth_mode == "dev" and os.getenv("K_SERVICE"):
            raise ValueError("Development authentication is forbidden on Cloud Run")
        if os.getenv("K_SERVICE") and self.storage_backend != "gcp":
            raise ValueError("Cloud Run requires persistent cloud storage")
        if self.firestore_database == "(default)":
            raise ValueError("Use a dedicated named Firestore database")
        if not self.api_url:
            self.api_url = (
                "http://localhost:8080"
                if self.storage_backend == "local"
                else f"https://{self.api_service}-{self.gcp_project_number}.{self.region}.run.app"
            )
        if not self.viewer_url:
            self.viewer_url = (
                "http://localhost:8081"
                if self.storage_backend == "local"
                else f"https://{self.viewer_service}-{self.gcp_project_number}.{self.region}.run.app"
            )
        if self.storage_backend == "local" and not self.cookie_signing_key:
            self.cookie_signing_key = "local-development-" + "cookie-key-only"
        if self.umami_enabled:
            from urllib.parse import urlsplit

            for field in ("umami_url", "umami_api_url", "umami_script_url"):
                value = getattr(self, field)
                if not value and field != "umami_url":
                    value = self.umami_url.rstrip("/") + (
                        "/api" if field == "umami_api_url" else "/script.js"
                    )
                    setattr(self, field, value)
                parsed = urlsplit(value)
                if (
                    parsed.scheme != "https"
                    or not parsed.hostname
                    or parsed.username
                    or parsed.password
                    or parsed.query
                    or parsed.fragment
                ):
                    raise ValueError(
                        f"{field.upper()} must be an HTTPS URL without credentials, query or fragment"
                    )
        return self


def csv(value: str) -> set[str]:
    return {item.strip().lower() for item in value.split(",") if item.strip()}
