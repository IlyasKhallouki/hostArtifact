"""Exercise actual HTTP MCP and viewer processes with temporary local storage."""

import asyncio
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from fastmcp import Client


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def run(api, viewer):
    async with (
        Client(api + "/mcp") as mcp,
        httpx.AsyncClient(follow_redirects=False) as browser,
    ):
        result = (
            await mcp.call_tool(
                "publish_artifact",
                {"client": "Acceptance", "title": "Sample", "html": "<h1>Before</h1>"},
            )
        ).data
        identifier, url = result["id"], result["url"]
        assert (await browser.get(url)).status_code == 200
        update = (
            await mcp.call_tool(
                "update_artifact",
                {
                    "artifact": identifier,
                    "edits": [{"find": "Before", "replace": "After"}],
                },
            )
        ).data
        assert update["url"] == url and "After" in (await browser.get(url)).text
        await mcp.call_tool(
            "set_access",
            {
                "artifact": identifier,
                "mode": "password",
                "password": "local-acceptance-password",
            },
        )
        assert (await browser.get(url)).status_code == 401
        assert (
            await browser.post(url, data={"password": "incorrect"})
        ).status_code == 401
        assert (
            await browser.post(url, data={"password": "local-acceptance-password"})
        ).status_code == 303
        assert (await browser.get(url)).status_code == 200
        await mcp.call_tool(
            "set_access", {"artifact": identifier, "expires_at": "2020-01-01"}
        )
        assert (await browser.get(url)).status_code == 410
        await mcp.call_tool(
            "set_access", {"artifact": identifier, "clear_expiry": True, "mode": "link"}
        )
        await mcp.call_tool("rollback_artifact", {"artifact": identifier, "version": 1})
        assert "Before" in (await browser.get(url)).text
        await mcp.call_tool("unpublish_artifact", {"artifact": identifier})
        assert (await browser.get(url)).status_code == 404
        await mcp.call_tool("restore_artifact", {"artifact": identifier})
        assert (await browser.get(url)).status_code == 200
        rotated = (
            await mcp.call_tool(
                "set_access", {"artifact": identifier, "rotate_link": True}
            )
        ).data
        assert (await browser.get(url)).status_code == 404
        assert (await browser.get(rotated["url"])).status_code == 200
        await mcp.call_tool("unpublish_artifact", {"artifact": identifier})
        await mcp.call_tool(
            "delete_artifact_permanently",
            {"artifact": identifier, "confirm_title": "Sample"},
        )
        assert (await browser.get(rotated["url"])).status_code == 404
    print(
        "Local HTTP acceptance passed: publish, update, password, expiry, rollback, unpublish, restore, rotate, delete"
    )


def main():
    api_port, viewer_port = port(), port()
    api, viewer = f"http://127.0.0.1:{api_port}", f"http://127.0.0.1:{viewer_port}"
    processes = []
    with tempfile.TemporaryDirectory() as directory:
        env = {
            **os.environ,
            "AUTH_MODE": "dev",
            "STORAGE_BACKEND": "local",
            "LOCAL_DATA": directory,
            "API_URL": api,
            "VIEWER_URL": viewer,
            "ADMIN_EMAILS": "dev@local",
        }
        log_path = Path(directory) / "runtime.log"
        with log_path.open("w") as log:
            try:
                for mode, number in (("api", api_port), ("viewer", viewer_port)):
                    processes.append(
                        subprocess.Popen(
                            [
                                sys.executable,
                                "-m",
                                "uvicorn",
                                "publisher.main:create_app",
                                "--factory",
                                "--host",
                                "127.0.0.1",
                                "--port",
                                str(number),
                                "--no-access-log",
                                "--no-proxy-headers",
                            ],
                            env={**env, "APP_MODE": mode},
                            stdout=log,
                            stderr=log,
                        )
                    )
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    try:
                        if (
                            httpx.get(api + "/health").status_code == 200
                            and httpx.get(viewer + "/health").status_code == 200
                        ):
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.1)
                else:
                    raise RuntimeError("Local processes did not become healthy")
                asyncio.run(run(api, viewer))
            finally:
                for process in processes:
                    process.terminate()
                for process in processes:
                    process.wait(timeout=10)


if __name__ == "__main__":
    main()
