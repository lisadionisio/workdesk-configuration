"""Explicit host-local Google account selection for Workdesk jobs.

This verifies the authenticated principal, not permission to send or mutate.
Callers retain their own operation approval and job ownership requirements.
Only the two field-verified CLI versions are supported until revalidated.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys


SUPPORTED_LAYOUTS = {"0.4.1": "legacy-account", "0.22.5": "config-dir"}
IDENTITY_ENV = (
    "GOOGLE_WORKSPACE_CLI_TOKEN", "GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE",
    "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_WORKSPACE_CLI_ACCOUNT",
    "GOOGLE_WORKSPACE_CLI_IMPERSONATED_USER", "GOOGLE_WORKSPACE_CLI_CONFIG_DIR",
    "GOOGLE_WORKSPACE_CLI_CLIENT_ID", "GOOGLE_WORKSPACE_CLI_CLIENT_SECRET",
    "GOOGLE_WORKSPACE_PROJECT_ID", "GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND",
    "GOOGLE_WORKSPACE_CLI_LOG", "GOOGLE_WORKSPACE_CLI_LOG_FILE",
)


class AccountError(RuntimeError):
    """Safe diagnostic: never include raw provider output or credentials."""


def unique_mapping(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate account configuration key")
        result[key] = value
    return result


def account_route(account, environ=None):
    env = dict(os.environ if environ is None else environ)
    if not isinstance(account, str) or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", account):
        raise AccountError("An explicit Google account email is required")
    try:
        raw = env.get("WORKDESK_GWS_ACCOUNTS")
        if raw is None:
            state = Path(env.get("WORKDESK_STATE_HOME", str(Path.home()/".local/state/workdesk")))
            raw = (state/"gws-accounts.json").read_text()
        routes = json.loads(raw, object_pairs_hook=unique_mapping)
        if not isinstance(routes, dict):
            raise ValueError("Account map must be an object")
        route = routes[account]
        if not isinstance(route, dict):
            raise ValueError("Account route must be an object")
    except (OSError, ValueError, KeyError, TypeError):
        raise AccountError("Explicit Google account routing is missing or invalid") from None
    for key in IDENTITY_ENV:
        env.pop(key, None)
    mode = route.get("mode")
    if mode == "legacy-account" and set(route) == {"mode"}:
        env["GOOGLE_WORKSPACE_CLI_ACCOUNT"] = account
    elif mode == "config-dir" and set(route) == {"mode", "config_dir"}:
        raw = route["config_dir"]
        if not isinstance(raw, str) or not raw:
            raise AccountError("Google credential directory is invalid")
        path = Path(raw).expanduser()
        if not path.is_absolute() or not path.is_dir():
            raise AccountError("Google credential directory must exist and be absolute")
        env["GOOGLE_WORKSPACE_CLI_CONFIG_DIR"] = str(path)
    else:
        raise AccountError("Unsupported Google account route; no default fallback")
    return mode, env


def verified_environment(binary, account, environ=None, run=subprocess.run):
    """Return the exact child environment after version and Drive identity checks.

    No login, token export, account change, or requested data operation is run.
    Callers must pass this returned environment to their actual subprocess.
    """
    program = Path(binary)
    if not program.is_absolute() or not program.is_file() or not os.access(program, os.X_OK):
        raise AccountError("Select an absolute executable Google CLI path")
    mode, env = account_route(account, environ)
    try:
        version = run([str(program), "--version"], env=env, capture_output=True, text=True, timeout=15)
        first = version.stdout.splitlines()[0] if version.stdout else ""
        match = re.fullmatch(r"gws (\d+\.\d+\.\d+)", first.strip())
        if version.returncode or not match or SUPPORTED_LAYOUTS.get(match[1]) != mode:
            raise AccountError("Google CLI version and account route are not a verified combination")
        result = run([str(program), "drive", "about", "get", "--params",
                      '{"fields":"user(emailAddress)"}'],
                     env=env, capture_output=True, text=True, timeout=90)
        data = json.loads(result.stdout, object_pairs_hook=unique_mapping)
        email = data["user"]["emailAddress"]
        if result.returncode or "error" in data or not isinstance(email, str) or email.casefold() != account.casefold():
            raise AccountError("Google account identity verification failed; requested operation was not run")
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
        raise AccountError("Google account verification unavailable; requested operation was not run") from None
    return env


def select_command(args, environ=None):
    """Consume the wrapper's account selector without passing it to native gws."""
    env = os.environ if environ is None else environ
    selected = None
    remaining = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--account" or arg.startswith("--account="):
            if selected is not None:
                raise AccountError("Specify the Google account only once")
            if arg == "--account":
                index += 1
                if index >= len(args):
                    raise AccountError("The account selector requires an email")
                selected = args[index]
            else:
                selected = arg.split("=", 1)[1]
            if not selected:
                raise AccountError("The account selector requires an email")
        else:
            remaining.append(arg)
        index += 1
    selected = selected if selected is not None else env.get("WORKDESK_GWS_ACCOUNT", env.get("GOOGLE_WORKSPACE_CLI_ACCOUNT"))
    if not remaining or remaining[0] == "auth":
        raise AccountError("Use the dedicated authentication flow for Google auth commands")
    return selected, remaining


def main():
    # Internal entry point used by the sourced shell function. execve preserves
    # native argument boundaries, streams, signals and the requested exit code.
    if len(sys.argv) < 3:
        raise AccountError("Expected an executable path and Google command")
    binary = sys.argv[1]
    account, args = select_command(sys.argv[2:])
    env = verified_environment(binary, account)
    os.execve(binary, [binary] + args, env)


if __name__ == "__main__":
    try:
        main()
    except AccountError as error:
        print("ERROR: " + str(error), file=sys.stderr)
        sys.exit(2)
