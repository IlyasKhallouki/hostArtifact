from urllib.parse import urlsplit

from publisher.models import PublisherError


def active_domains(state):
    return {
        name: item
        for name, item in state["domains"].items()
        if item["status"] == "active"
    }


def require_domain(state, domain):
    if domain is not None and domain not in active_domains(state):
        raise PublisherError(
            f"INVALID_INPUT: Choose an active domain: {', '.join(active_domains(state)) or 'none yet; ask your administrator'}."
        )
    return domain


def canonical_base(state, artifact, settings):
    active = active_domains(state)
    # Domain is snapshotted when a page is published; future client/global changes do not move it.
    assigned = artifact.get("domain")
    if assigned in active:
        return f"https://{assigned}"
    return settings.viewer_url.rstrip("/")


def default_domain(state, client):
    active = active_domains(state)
    candidate = state["clients"].get(client, {}).get("default_domain")
    if candidate in active:
        return candidate
    return next((name for name, item in active.items() if item.get("is_default")), None)


def artifact_url(state, artifact, settings):
    return f"{canonical_base(state, artifact, settings)}/{artifact['client_slug']}/{artifact['slug']}-{artifact['url_token']}"


def known_hosts(state, settings):
    return set(active_domains(state)) | {urlsplit(settings.viewer_url).hostname}
