---
name: publish-artifact
description: Publish, host, share, update, password-protect, list or take down client-facing HTML pages, dashboards, reports, demos and artifacts on the configured publishing service and return a shareable link.
---
# Publishing client pages
Use the publisher connector. If the connector is missing, ask the user to install the configured plugin from their administrator and connect it with an allowed Google account. If access is blocked, call whoami when available and direct the user to the deployment administrator.
Keep replies short. Avoid technical details unless asked.

## Publish
1. Identify the current artifact. Ask if there are several candidates.
2. Use the full HTML source. Convert React/JSX or Markdown into standalone HTML first. Embed assets or use public URLs. Remove Claude-only APIs such as window.claude and window.storage; explain any lost functionality before publishing. Never include passwords or private notes.
3. Call list_clients and reuse a matching client. Call list_artifacts to check for an existing page; ask whether to update that page or create another link.
4. Honor requested access and expiry. Otherwise use the default and mention that a password or expiry can be added.
5. For a custom domain, call list_domains and choose an active domain. New domains require the admin; never promise an undiscovered hostname is available.
6. Call publish_artifact. Return the link, who can open it, password if generated, and expiry. Suggest sending passwords separately.

## Update
Resolve a pasted link or use list_artifacts. If necessary fetch get_artifact(include_html=true), continuing with next_offset while truncated. For small changes use exact find/replace edits from the current HTML. For larger changes use full html. Confirm the same link now shows the changes.

## Manage
- List shared work with list_artifacts.
- Password, expiry or a leaked link: set_access; rotate_link invalidates the old URL.
- Domain change: set_artifact_domain. Existing links redirect while their domains remain attached. set_client_domain affects future pages only.
- Undo: list_versions, then rollback_artifact.
- Take down only on explicit request; confirm the page and use unpublish_artifact. It can be restored.
- Permanent deletion requires an admin, an unpublished page and exact title confirmation. Prefer unpublishing unless the user insists.
- On FORBIDDEN, call whoami and explain its precise reason and fix. Never suggest granting broad cloud roles to a colleague who only needs publishing access.

## Analytics

When a tool result includes `analytics.dashboard_url`, include that Umami dashboard link alongside the page link. If analytics is pending, explain that the page is published and its analytics setup needs a retry. Automatic tracking is handled by the viewer; do not embed another Umami script in the HTML.
