# Sharing pages from Claude

Get the configured plugin from your administrator, install it in Claude, and connect its publisher connector. Sign in with the Google account your administrator has allowed.

Build a page in Claude, then ask:

- "Publish this for Acme."
- "Update the Acme dashboard with these changes."
- "Put a password on it."
- "Expire this link on November 30."
- "What have we shared with Acme?"
- "Use our other sharing domain for this page."
- "Undo the last update."
- "Take it down."

Claude returns a link. Anyone with an ordinary link can open it. A password-protected page asks for a password; send that separately from the link. Expired pages show an expiry message. You can restore pages you've taken offline.

Updates keep the same link. If a link has been shared too widely, ask Claude to replace it; the old link will stop working. Moving a page to another connected domain preserves the old link while both domains remain active. Ask your administrator to add a new domain.

Use complete HTML pages with embedded images or images at public URLs. Features that depend on Claude's own runtime need to be adapted before publishing. Review the page for private notes, credentials and information you don't intend to share.

If a tool refuses access, ask "Which account am I using, and what access am I missing?" Claude can report the signed-in account and the reason. Send that explanation to your administrator. They don't need your Google password.
