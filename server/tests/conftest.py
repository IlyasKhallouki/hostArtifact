import pytest

from publisher.config import Settings
from publisher.service import ArtifactService
from publisher.storage import create_stores


@pytest.fixture
def service(tmp_path):
    settings = Settings(
        _env_file=None,
        auth_mode="dev",
        storage_backend="local",
        local_data=tmp_path,
        admin_emails="dev@local",
        allowed_email_domains="example.com",
        viewer_url="https://viewer.example.com",
    )
    metadata, blobs = create_stores(settings)
    return ArtifactService(metadata, blobs, settings)
