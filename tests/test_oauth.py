"""OAuth and MCP auth route regressions."""

from __future__ import annotations

import base64
import hashlib
from urllib.parse import parse_qs, urlparse

from starlette.testclient import TestClient

from mcp_nexus.config import Settings
from mcp_nexus.server import create_app


def _pkce_pair(verifier: str) -> tuple[str, str]:
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def _oauth_settings() -> Settings:
    settings = Settings()
    settings.public_base_url = "https://example.com"
    settings.oauth_issuer = "https://example.com"
    settings.mcp_path = "/mcp/nexus"
    settings.mcp_path_aliases = ["/mcp"]
    settings.ssh_host = "127.0.0.1"
    settings.ssh_user = "root"
    settings.ssh_password = ""
    settings.ssh_port = 22
    return settings


def test_mcp_requires_auth_and_exposes_metadata():
    app = create_app(_oauth_settings(), enable_watchdog=False)

    with TestClient(app) as client:
        response = client.post("/mcp/nexus", json={})
        assert response.status_code == 401
        header = response.headers["www-authenticate"]
        assert 'error="invalid_token"' in header
        assert (
            'resource_metadata="https://example.com/.well-known/oauth-protected-resource/mcp/nexus"' in header
        )

        protected_resource = client.get("/.well-known/oauth-protected-resource/mcp/nexus")
        assert protected_resource.status_code == 200
        assert protected_resource.json()["resource"] == "https://example.com/mcp/nexus"

        metadata = client.get("/.well-known/oauth-authorization-server")
        assert metadata.status_code == 200
        payload = metadata.json()
        assert payload["authorization_endpoint"] == "https://example.com/authorize"
        assert payload["token_endpoint"] == "https://example.com/token"
        assert payload["registration_endpoint"] == "https://example.com/register"


def test_mcp_browser_get_renders_connect_landing_without_breaking_auth_surface():
    app = create_app(_oauth_settings(), enable_watchdog=False)

    with TestClient(app) as client:
        landing = client.get("/mcp/nexus", headers={"Accept": "text/html"})
        assert landing.status_code == 200
        assert "Connect your AI to a real server." in landing.text
        assert "View On GitHub" in landing.text
        assert "Manual OAuth Values" in landing.text

        api_like = client.get("/mcp/nexus", headers={"Accept": "application/json"})
        assert api_like.status_code == 401


def test_oauth_authorization_code_flow_completes_against_consent_route():
    app = create_app(_oauth_settings(), enable_watchdog=False)
    verifier, challenge = _pkce_pair("codex-test-verifier")

    with TestClient(app) as client:
        registration = client.post(
            "/register",
            json={
                "redirect_uris": ["https://chat.openai.com/aip/callback"],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "client_secret_post",
            },
        )
        assert registration.status_code == 201
        client_info = registration.json()

        authorize = client.get(
            "/authorize",
            params={
                "client_id": client_info["client_id"],
                "redirect_uri": "https://chat.openai.com/aip/callback",
                "response_type": "code",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "state-1",
                "resource": "https://example.com/mcp/nexus",
            },
            follow_redirects=False,
        )
        assert authorize.status_code == 302

        consent_location = urlparse(authorize.headers["location"])
        assert consent_location.path == "/oauth/consent"
        request_id = parse_qs(consent_location.query)["request_id"][0]

        consent_page = client.get(f"/oauth/consent?request_id={request_id}")
        assert consent_page.status_code == 200
        assert "Authorize ChatGPT" in consent_page.text

        approval = client.post(
            "/oauth/consent",
            data={
                "request_id": request_id,
                "decision": "approve",
                "ssh_host": "127.0.0.1",
                "ssh_user": "root",
                "ssh_port": "22",
                "ssh_password": "",
            },
            follow_redirects=False,
        )
        assert approval.status_code == 302

        callback_location = urlparse(approval.headers["location"])
        assert callback_location.path == "/aip/callback"
        callback_query = parse_qs(callback_location.query)
        code = callback_query["code"][0]
        assert callback_query["state"] == ["state-1"]

        token = client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": "https://chat.openai.com/aip/callback",
                "client_id": client_info["client_id"],
                "client_secret": client_info["client_secret"],
                "code_verifier": verifier,
                "resource": "https://example.com/mcp/nexus",
            },
        )
        assert token.status_code == 200
        token_payload = token.json()
        assert token_payload["token_type"] == "Bearer"
        assert token_payload["access_token"]
        assert token_payload["refresh_token"]
        assert token_payload["scope"] == "nexus"


def test_static_oauth_client_authorization_code_flow_completes_without_dynamic_registration():
    settings = _oauth_settings()
    settings.oauth_client_id = "chatgpt-manual"
    settings.oauth_client_secret = "static-secret"
    settings.oauth_client_redirect_uris = ["https://chatgpt.com/connector/oauth/test"]
    app = create_app(settings, enable_watchdog=False)
    verifier, challenge = _pkce_pair("codex-static-client-verifier")

    with TestClient(app) as client:
        authorize = client.get(
            "/authorize",
            params={
                "client_id": "chatgpt-manual",
                "redirect_uri": "https://chatgpt.com/connector/oauth/test",
                "response_type": "code",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "state-static",
                "resource": "https://example.com/mcp/nexus",
            },
            follow_redirects=False,
        )
        assert authorize.status_code == 302

        consent_location = urlparse(authorize.headers["location"])
        request_id = parse_qs(consent_location.query)["request_id"][0]

        approval = client.post(
            "/oauth/consent",
            data={
                "request_id": request_id,
                "decision": "approve",
                "ssh_host": "127.0.0.1",
                "ssh_user": "root",
                "ssh_port": "22",
                "ssh_password": "",
            },
            follow_redirects=False,
        )
        assert approval.status_code == 302

        callback_location = urlparse(approval.headers["location"])
        callback_query = parse_qs(callback_location.query)
        code = callback_query["code"][0]
        assert callback_query["state"] == ["state-static"]

        token = client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": "https://chatgpt.com/connector/oauth/test",
                "client_id": "chatgpt-manual",
                "client_secret": "static-secret",
                "code_verifier": verifier,
                "resource": "https://example.com/mcp/nexus",
            },
        )
        assert token.status_code == 200
        token_payload = token.json()
        assert token_payload["token_type"] == "Bearer"
        assert token_payload["access_token"]
