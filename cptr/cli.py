import asyncio
import hashlib
import json
import os
import sys
import time
import uuid

import click
import uvicorn

DEFAULT_MANAGED_MODEL_ID = "cptr"
DEFAULT_MANAGED_MODEL_NAME = "Open WebUI Computer"
DEFAULT_MANAGED_MODEL_OWNER = "cptr"


@click.group()
def cli():
    """Your computer, from anywhere."""
    pass


@cli.command()
@click.option(
    "--host",
    default="127.0.0.1",
    show_default=True,
    help="Host to bind to. Use 0.0.0.0 to allow access from other devices.",
)
@click.option("--port", default=8000, type=int, help="Port to bind to.")
@click.option("--reload", is_flag=True, default=False, help="Enable auto-reload.")
@click.option("--headless", is_flag=True, default=False, help="Don't open browser.")
def run(host: str, port: int, reload: bool, headless: bool):
    """Start the cptr server."""
    import os
    import secrets

    display_host = "localhost" if host == "0.0.0.0" else host

    token = secrets.token_hex(32)
    os.environ["CPTR_STARTUP_TOKEN"] = token
    os.environ["CPTR_PORT"] = str(port)
    url = f"http://{display_host}:{port}/?token={token}"

    print(f"\n  ➜  {url}\n")
    if not headless:
        import threading
        import webbrowser

        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(
        "cptr.app:application",
        host=host,
        port=port,
        reload=reload,
    )


def _required_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise click.UsageError(f"{name} must be set")
    return value


@cli.command()
@click.option("--username", default="wu-desktop", show_default=True)
@click.option("--password-env", default="CPTR_BOOTSTRAP_PASSWORD", show_default=True)
@click.option("--gateway-key-env", default="CPTR_GATEWAY_KEY", show_default=True)
@click.option("--workspace", type=click.Path(path_type=str), required=True)
@click.option("--upstream-url", default="", help="Optional OpenAI-compatible model API base URL.")
@click.option("--upstream-api-key-env", default="CPTR_UPSTREAM_API_KEY", show_default=True)
@click.option(
    "--upstream-model",
    default="",
    help="Optional model-ID override. Defaults to the configured managed model ID.",
)
def bootstrap(
    username: str,
    password_env: str,
    gateway_key_env: str,
    workspace: str,
    upstream_url: str,
    upstream_api_key_env: str,
    upstream_model: str,
):
    """Provision a local Computer instance without opening a browser.

    Secrets are read only from the environment so they never appear in the
    process arguments. The command is idempotent and prints only its result
    JSON to stdout, allowing a desktop launcher to consume it safely.
    """
    password = _required_env(password_env)
    gateway_key = _required_env(gateway_key_env)
    upstream_api_key = os.environ.get(upstream_api_key_env, "")
    managed_model = {
        "id": os.environ.get("CPTR_MANAGED_MODEL_ID", DEFAULT_MANAGED_MODEL_ID).strip()
        or DEFAULT_MANAGED_MODEL_ID,
        "name": os.environ.get("CPTR_MANAGED_MODEL_NAME", DEFAULT_MANAGED_MODEL_NAME).strip()
        or DEFAULT_MANAGED_MODEL_NAME,
        "owner": os.environ.get("CPTR_MANAGED_MODEL_OWNER", DEFAULT_MANAGED_MODEL_OWNER).strip()
        or DEFAULT_MANAGED_MODEL_OWNER,
    }
    github_mcp_path = os.environ.get("CPTR_GITHUB_MCP_PATH", "").strip()
    github_mcp_toolsets = os.environ.get(
        "CPTR_GITHUB_MCP_TOOLSETS",
        "all",
    ).strip()

    async def provision() -> dict:
        import httpx
        from pathlib import Path

        from cptr.models import Auth, Config, User
        from cptr.models.workspaces import Workspace
        from cptr.utils.config import _get_jwt_secret, hash_password, now_ms
        from cptr.utils.crypto import encrypt_key
        from cptr.utils.db import init_db

        await init_db()
        username_value = username.strip()
        if not username_value:
            raise click.UsageError("username cannot be empty")

        auth = await Auth.get_by_username(username_value)
        if auth:
            user_id = auth.user_id
        else:
            user_id = await User.create(
                username=username_value,
                password_hash=hash_password(password),
                role="admin",
                created_at=now_ms(),
            )

        workspace_path = str(Path(workspace).expanduser().resolve())
        await Workspace.upsert(
            user_id=user_id,
            path=workspace_path,
            name=Path(workspace_path).name or workspace_path,
            data={},
        )

        # The managed surface is focused on one model. Keep all optional
        # post-turn LLM work disabled: on a local or shared inference server
        # it competes with the user's next request and adds no user value.
        await Config.upsert(
            {
                "chat.title_generation.enabled": False,
                "memory.background_review_enabled": False,
                "skills.background_review_enabled": False,
                "gateway.managed_model": managed_model,
            }
        )

        # Desktop may provide GitHub's official MCP binary. Register it as a
        # managed stdio server so its OAuth flow and tools stay local to the
        # runtime user, without exposing a token to Open WebUI or the model.
        if github_mcp_path:
            tool_servers = await Config.get("tool_servers") or []
            github_server = next(
                (
                    server
                    for server in tool_servers
                    if isinstance(server, dict) and server.get("id") == "github"
                ),
                None,
            )
            managed_github_server = {
                "id": "github",
                "type": "mcp_stdio",
                "name": "GitHub",
                "description": "Managed read-only GitHub MCP tools",
                "enabled": True,
                "command": sys.executable,
                "args": ["-m", "cptr.github_mcp", github_mcp_path],
                "env": {"GITHUB_TOOLSETS": github_mcp_toolsets},
                "cwd": workspace_path,
                "managed_by": "wu-desktop",
            }
            if github_server is None:
                tool_servers.append(managed_github_server)
                await Config.upsert({"tool_servers": tool_servers})
            elif github_server.get("managed_by") == "wu-desktop":
                github_server.update(managed_github_server)
                await Config.upsert({"tool_servers": tool_servers})

        keys = await Config.get("api_keys") or []
        key_hash = hashlib.sha256(gateway_key.encode()).hexdigest()
        if not any(entry.get("key_hash") == key_hash for entry in keys if isinstance(entry, dict)):
            keys.append(
                {
                    "id": str(uuid.uuid4()),
                    "key_hash": key_hash,
                    "user_id": user_id,
                    "name": "wu-desktop",
                    "created_at": int(time.time()),
                }
            )
            await Config.upsert({"api_keys": keys})

        selected_model = upstream_model.strip()
        normalized_upstream_url = upstream_url.rstrip("/")
        if normalized_upstream_url and not selected_model:
            headers = {"Authorization": f"Bearer {upstream_api_key}"} if upstream_api_key else {}
            payload: dict = {}
            discovery_error: str | None = None
            for attempt in range(1, 4):
                try:
                    async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
                        response = await client.get(f"{normalized_upstream_url}/models", headers=headers)
                        response.raise_for_status()
                        payload = response.json()
                    break
                except httpx.HTTPStatusError as exc:
                    # A bad credential or an access policy will not improve on retry.
                    discovery_error = f"Main server rejected model discovery with HTTP {exc.response.status_code}"
                    break
                except httpx.HTTPError:
                    discovery_error = "Unable to reach the main server for model discovery"
                    if attempt < 3:
                        await asyncio.sleep(attempt)
                except ValueError:
                    discovery_error = "Main server returned an invalid model-discovery response"
                    break

            if discovery_error:
                click.echo(
                    f"{discovery_error} after {attempt} attempt(s); "
                    "starting without a cached upstream model.",
                    err=True,
                )

            models = payload.get("data", []) if isinstance(payload, dict) else []
            selected_model = next(
                (
                    item["id"].strip()
                    for item in models
                    if isinstance(item, dict)
                    and isinstance(item.get("id"), str)
                    and item["id"].strip() == managed_model["id"]
                ),
                "",
            )
            if not selected_model:
                click.echo(
                    f"Main server does not expose the required model ID '{managed_model['id']}'; "
                    "starting without an upstream model.",
                    err=True,
                )
            else:
                click.echo(f"Selected main-server model: {selected_model}", err=True)

        if normalized_upstream_url:
            connections = await Config.get("chat.connections") or []
            connection = next(
                (item for item in connections if isinstance(item, dict) and item.get("id") == "wu-main-server"),
                None,
            )
            if connection is None:
                connection = {"id": "wu-main-server"}
                connections.append(connection)
            connection.update(
                {
                    "name": "Main server",
                    "provider": "openai",
                    "api_type": "chat_completions",
                    "provider_type": "default",
                    "prefix_id": None,
                    "base_url": normalized_upstream_url,
                    "enabled": True,
                    "data": {"models": [selected_model]} if selected_model else {},
                }
            )
            if upstream_api_key:
                connection["api_key"] = encrypt_key(upstream_api_key, _get_jwt_secret())
            await Config.upsert({"chat.connections": connections})
            if selected_model:
                await Config.upsert({"chat.default_model": selected_model})

        return {
            "ok": True,
            "workspace": workspace_path,
            "gateway_key": gateway_key,
            "upstream_model": selected_model,
            "github_mcp_enabled": bool(github_mcp_path),
        }

    click.echo(json.dumps(asyncio.run(provision())))


def main():
    cli()


if __name__ == "__main__":
    main()
