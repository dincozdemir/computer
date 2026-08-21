"""Launch GitHub's official MCP server with the runtime user's gh credential.

The official native server accepts a GitHub personal-access token through its
environment.  This adapter retrieves the token from GitHub CLI's secure
credential store only for the lifetime of the MCP child process; it is never
stored in Computer configuration or surfaced to the model.
"""

from __future__ import annotations

import os
import subprocess
import sys


def _start_browser_login() -> None:
    """Start GitHub CLI's one-time browser sign-in without blocking MCP."""
    try:
        subprocess.Popen(
            [
                "gh",
                "auth",
                "login",
                "--hostname",
                "github.com",
                "--web",
                "--git-protocol",
                "https",
                "--skip-ssh-key",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError:
        return


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python -m cptr.github_mcp /path/to/github-mcp-server", file=sys.stderr)
        raise SystemExit(2)

    binary = sys.argv[1]
    try:
        result = subprocess.run(
            ["gh", "auth", "token", "--hostname", "github.com"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        print("GitHub CLI (gh) is required but was not found.", file=sys.stderr)
        raise SystemExit(1)

    token = result.stdout.strip()
    if result.returncode != 0 or not token:
        _start_browser_login()
        print(
            "GitHub sign-in was opened in your browser. Complete it, then repeat your request.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    env = os.environ.copy()
    env["GITHUB_PERSONAL_ACCESS_TOKEN"] = token
    # This is an application policy boundary, not an instruction for the model.
    # GitHub MCP will refuse repository, pull-request, issue, and other
    # mutations even if a model attempts to invoke a write-capable tool.
    os.execve(binary, [binary, "stdio", "--read-only"], env)


if __name__ == "__main__":
    main()
