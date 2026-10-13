import json
import os
import shlex
import subprocess


class CloudError(RuntimeError):
    def __init__(self, command, reason, detail=""):
        self.command, self.reason, self.detail = command, reason, detail
        super().__init__(f"{reason}: {shlex.join(command)}")


def classify(stderr):
    lower = stderr.lower()
    if (
        "service_disabled" in lower
        or "has not been used" in lower
        or "is not enabled" in lower
    ):
        return "api_disabled"
    if "reauth" in lower or "invalid_grant" in lower or "no active account" in lower:
        return "authentication_required"
    if (
        "permission_denied" in lower
        or "does not have permission" in lower
        or "403" in lower
    ):
        return "permission_denied"
    if (
        "not_found" in lower
        or "cannot find service [" in lower
        or "was not found" in lower
        or "does not exist" in lower
        or "404" in lower
    ):
        return "not_found"
    return "command_failed"


class Gcloud:
    def __init__(self, project, progress=None):
        self.project = project
        self.progress = progress or (lambda message: None)

    def _execute(self, command, timeout, label):
        self.progress(f"{label} (timeout {timeout}s)")
        try:
            return subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                env={
                    **os.environ,
                    "CLOUDSDK_CORE_DISABLE_PROMPTS": "1",
                    "CLOUDSDK_CORE_HTTP_TIMEOUT": "20",
                },
            )
        except subprocess.TimeoutExpired:
            raise CloudError(
                command,
                "timeout",
                f"Google Cloud did not finish within {timeout} seconds. Check resource status before retrying a change.",
            ) from None

    def run(self, *args):
        command = [
            "gcloud",
            *args,
            f"--project={self.project}",
            "--quiet",
            "--format=json",
        ]
        timeout = (
            1800
            if args[:2] == ("builds", "submit")
            else 600
            if args[:2] == ("run", "deploy")
            else 30
            if any(arg in {"describe", "list", "list-user-verified"} for arg in args)
            else 180
        )
        result = self._execute(command, timeout, "Running gcloud " + " ".join(args[:4]))
        if result.returncode:
            raise CloudError(command, classify(result.stderr))
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def access_token(self):
        result = self._execute(
            ["gcloud", "auth", "print-access-token", "--quiet"],
            30,
            "Checking Google credentials",
        )
        if result.returncode:
            raise CloudError(
                ["gcloud", "auth", "print-access-token"], "authentication_required"
            )
        return result.stdout.strip()

    def ensure(self, describe, create, expected=None):
        try:
            resource = self.run(*describe)
        except CloudError as exc:
            if exc.reason != "not_found":
                raise
            return self.run(*create)
        if expected and not expected(resource):
            raise CloudError(
                list(describe),
                "resource_conflict",
                "Existing resource does not match publisher configuration; refusing to overwrite it.",
            )
        return resource
