#!/usr/bin/env python3
"""Conversation-friendly front end to installed Hermes Anthropic OAuth support."""
import argparse
import getpass
import json
import os
from pathlib import Path
import secrets
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode


def profile_path():
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def helpers():
    sys.path.insert(0, str(profile_path() / "scripts"))
    from configure_multi_ai import atomic_write
    from agent import anthropic_credentials as native
    return native, atomic_write


def context_path():
    return profile_path() / ".claude-pkce-context.json"


def start():
    native, atomic_write = helpers()
    ctx = None
    if context_path().is_file():
        try:
            candidate = json.loads(context_path().read_text())
            if 0 <= time.time() - candidate["created_at"] <= 900 and all(candidate.get(k) for k in ("verifier", "challenge", "state")):
                ctx = candidate
        except (ValueError, KeyError, TypeError):
            pass
    # Repeated requests reuse the outstanding link instead of invalidating
    # the code the owner may already be copying from the browser.
    if ctx is None:
        verifier, challenge = native._generate_pkce()
        ctx = {"verifier": verifier, "challenge": challenge,
               "state": secrets.token_urlsafe(32), "created_at": time.time()}
        atomic_write(context_path(), json.dumps(ctx))
    os.chmod(context_path(), 0o600)
    params = {"code": "true", "client_id": native._OAUTH_CLIENT_ID,
              "response_type": "code", "redirect_uri": native._OAUTH_REDIRECT_URI,
              "scope": native._OAUTH_SCOPES, "code_challenge": ctx["challenge"],
              "code_challenge_method": "S256", "state": ctx["state"]}
    print("AUTH_URL: https://claude.ai/oauth/authorize?" + urlencode(params))


def validate_code(raw, context, now=None):
    age = (now or time.time()) - context["created_at"]
    if not 0 <= age <= 900:
        raise ValueError("Authorization expired; generate a new link")
    code, separator, state = raw.strip().partition("#")
    if not separator or not code or not secrets.compare_digest(state, context["state"]):
        raise ValueError("Invalid authorization state; send the complete code")
    return code, state


def configure_routes():
    from configure_multi_ai import main
    old_argv = sys.argv
    try:
        sys.argv = ["configure_multi_ai.py"]
        main()
    finally:
        sys.argv = old_argv


def persist(tokens):
    native, _ = helpers()
    from hermes_cli.auth import read_credential_pool, write_credential_pool, unsuppress_credential_source
    # Persist the refreshable native source before publishing its pool reference.
    native._write_hermes_oauth_credentials(tokens["access_token"], tokens.get("refresh_token"), tokens.get("expires_at_ms"))
    entries = read_credential_pool("anthropic")
    existing = next((e for e in entries if e.get("source") == "hermes_pkce"), None)
    item = dict(existing or {"id": secrets.token_hex(6), "priority": 0, "label": "Claude conectado pelo dono"})
    item.update(auth_type="oauth", source="hermes_pkce", **tokens)
    # A completed reauthorization revives only this account's refreshed entry.
    # Stale pool flags must not hide the newly connected subscription.
    for key in ("disabled", "revoked", "last_status"):
        item.pop(key, None)
    entries = [e for e in entries if e.get("id") != item["id"]] + [item]
    write_credential_pool("anthropic", entries)
    unsuppress_credential_source("anthropic", "hermes_pkce")
    from agent.credential_pool import load_pool
    load_pool("anthropic")


def exchange(raw):
    native, _ = helpers()
    ctx = json.loads(context_path().read_text())
    code, state = validate_code(raw, ctx)
    payload = json.dumps({"grant_type": "authorization_code", "client_id": native._OAUTH_CLIENT_ID,
                          "code": code, "state": state, "redirect_uri": native._OAUTH_REDIRECT_URI,
                          "code_verifier": ctx["verifier"]}).encode()
    result = None
    for endpoint in native._OAUTH_TOKEN_URLS:
        request = urllib.request.Request(endpoint, data=payload, method="POST",
                  headers={"Content-Type": "application/json", "User-Agent": native._OAUTH_TOKEN_USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                result = json.load(response)
            break
        except urllib.error.HTTPError as exc:
            if exc.code not in (404, 405):
                raise ValueError(f"Authorization exchange failed (HTTP {exc.code})") from None
    if not result or not result.get("access_token"):
        raise ValueError("Authorization exchange returned no credentials")
    tokens = {"access_token": result["access_token"], "refresh_token": result.get("refresh_token"),
              "expires_at_ms": int(time.time() * 1000 + float(result.get("expires_in", 3600)) * 1000)}
    persist(tokens)
    context_path().unlink()
    # Once credentials are stored, consume the one-time flow before changing
    # routes. A config failure can be resumed with finish, without new login.
    configure_routes()
    print("SUCCESS: Claude connected; verify inference and reload the gateway")


def import_existing():
    native, _ = helpers()
    candidates = []
    for path in [profile_path() / ".anthropic_oauth.json", Path.home() / ".hermes/.anthropic_oauth.json"]:
        if path.is_file():
            data = json.loads(path.read_text())
            if data.get("accessToken"):
                candidates.append(data)
    cli = native.read_claude_code_credentials()
    if cli:
        candidates.append(cli)
    if not candidates:
        raise ValueError("No existing Claude account; request login")
    data = max(candidates, key=lambda d: d.get("expiresAt") or 0)
    if (data.get("expiresAt") or 0) <= time.time() * 1000 and not data.get("refreshToken"):
        raise ValueError("Existing account expired without refresh; request login")
    persist({"access_token": data["accessToken"], "refresh_token": data.get("refreshToken"), "expires_at_ms": data.get("expiresAt")})
    configure_routes()
    print("SUCCESS: Existing local Claude account connected to this profile")


def cancel():
    # Cancel only the pending challenge. Connected subscriptions are untouched.
    context_path().unlink(missing_ok=True)
    print("CANCELLED: pending login cleared; existing accounts preserved")


def read_authorization_code():
    # The gateway can submit one line to a background PTY. getpass disables
    # echo so the authorization code does not appear in terminal output.
    if sys.stdin.isatty():
        return getpass.getpass("AUTH_CODE: ")
    return sys.stdin.read()


def reload_gateway():
    from gateway.status import get_running_pid
    from hermes_cli.gateway import _request_gateway_self_restart
    pid = get_running_pid()
    if not pid or not _request_gateway_self_restart(pid):
        raise ValueError("Run reload from this profile's gateway terminal")
    print("RELOAD_REQUESTED: gateway will reload after the active turn")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "exchange", "finish", "cancel", "import-existing", "reload"])
    args = parser.parse_args()
    try:
        if args.action == "start":
            start()
        elif args.action == "exchange":
            exchange(read_authorization_code())
        elif args.action == "import-existing":
            import_existing()
        elif args.action == "finish":
            helpers()
            configure_routes()
            print("SUCCESS: connected accounts configured; verify inference and reload the gateway")
        elif args.action == "cancel":
            cancel()
        else:
            reload_gateway()
    except Exception as exc:
        # Never expose provider response bodies, auth codes or tokens.
        print("ERROR:", type(exc).__name__, "Claude connection did not complete; inspect sanitized diagnostics", file=sys.stderr)
        raise SystemExit(2)
