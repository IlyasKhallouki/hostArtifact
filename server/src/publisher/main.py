from publisher.config import Settings
from publisher.logging_setup import configure
from publisher.service import ArtifactService
from publisher.storage import create_stores


def create_app():
    configure()
    settings = Settings()
    metadata, blobs = create_stores(settings)
    service = ArtifactService(metadata, blobs, settings)
    if settings.app_mode == "viewer":
        from publisher.viewer.app import create_viewer

        return create_viewer(service)
    from publisher.mcp_server import create_mcp

    return create_mcp(service).http_app(path="/mcp", stateless_http=True)
