"""Scan added staged lines without printing sensitive contents."""

import re
import subprocess

patch = subprocess.check_output(
    ["git", "diff", "--cached", "--no-ext-diff", "--unified=0"], text=True
)
patterns = [
    r"GOCSPX-[\w-]{12,}",
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY",
    r'"private_key"\s*:\s*"-----',
    r"AIza[\w-]{30,}",
    r"(?i)(?:SECRET|SIGNING_KEY|ENCRYPTION_KEY)\s*=\s*['\"]?[A-Za-z0-9_+/=-]{32,}",
]
for line in patch.splitlines():
    if (
        line.startswith("+")
        and not line.startswith("+++")
        and any(re.search(p, line) for p in patterns)
    ):
        raise SystemExit(
            "Secret-shaped value in staged changes. Remove it before committing."
        )
