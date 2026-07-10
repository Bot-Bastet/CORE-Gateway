"""Tests for /auth/register, /auth/login, and /accounts routes."""
import pytest


class TestAuthRegister:
    """POST /auth/register and POST /accounts."""

    def test_register_creates_account(self, client, auth_headers):
        """Registering a new user returns status=saved."""
        payload = {
            "email": "alice@example.com",
            "pseudo": "alice",
            "last_name": "Dupont",
            "first_name": "Alice",
            "phone": "0600000001",
            "password": "secret123",
            "preferences": {"dark_mode": True},
        }
        resp = client.post("/auth/register", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "saved"
        assert "Alice Dupont" in data["user"]

    def test_register_without_token_fails(self, client):
        """/accounts requires a token (register alias /auth/register does not)."""
        payload = {
            "email": "bob@example.com",
            "pseudo": "bob",
            "last_name": "Martin",
            "first_name": "Bob",
            "phone": "0600000002",
            "password": "secret456",
        }
        # /accounts requires auth
        resp = client.post("/accounts", json=payload)
        assert resp.status_code == 403

    def test_register_updates_existing(self, client, auth_headers):
        """Re-registering the same name updates the account."""
        payload = {
            "email": "carol@example.com",
            "pseudo": "carol",
            "last_name": "Leroy",
            "first_name": "Carol",
            "phone": "0600000003",
            "password": "pwd1",
        }
        r1 = client.post("/auth/register", json=payload)
        assert r1.status_code == 200

        # Update with new email
        payload["email"] = "carol_new@example.com"
        payload["password"] = "pwd2"
        r2 = client.post("/auth/register", json=payload)
        assert r2.status_code == 200
        assert r2.json()["status"] == "saved"

    def test_list_accounts(self, client, auth_headers):
        """GET /accounts returns all registered users."""
        # Register a user first
        client.post("/auth/register", json={
            "email": "dave@example.com",
            "pseudo": "dave",
            "last_name": "Smith",
            "first_name": "Dave",
            "phone": "0600000004",
            "password": "pw",
        })
        resp = client.get("/accounts", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)
        assert "Dave Smith" in data

    def test_delete_account(self, client, auth_headers):
        """DELETE /accounts/{name} removes the user."""
        client.post("/auth/register", json={
            "email": "eve@example.com",
            "pseudo": "eve",
            "last_name": "Jones",
            "first_name": "Eve",
            "phone": "0600000005",
            "password": "pw",
        })
        resp = client.delete("/accounts/Eve Jones", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"


class TestAuthLogin:
    """POST /auth/login."""

    def test_login_success(self, client):
        """Login with correct credentials returns status=success + api_token."""
        # Register first
        client.post("/auth/register", json={
            "email": "frank@example.com",
            "pseudo": "frank",
            "last_name": "Brown",
            "first_name": "Frank",
            "phone": "0600000006",
            "password": "correct-horse-battery-staple",
        })
        resp = client.post("/auth/login", json={
            "email": "frank@example.com",
            "password": "correct-horse-battery-staple",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["api_token"] == "test-token"
        assert data["user"]["email"] == "frank@example.com"
        # password_hash must NOT leak
        assert "password_hash" not in data["user"]

    def test_login_wrong_password(self, client):
        """Login with wrong password returns 401."""
        client.post("/auth/register", json={
            "email": "grace@example.com",
            "pseudo": "grace",
            "last_name": "Hopper",
            "first_name": "Grace",
            "phone": "0600000007",
            "password": "right-password",
        })
        resp = client.post("/auth/login", json={
            "email": "grace@example.com",
            "password": "wrong-password",
        })
        assert resp.status_code == 401
        assert "Mot de passe incorrect" in resp.json()["detail"]

    def test_login_nonexistent_user(self, client):
        """Login with unknown email returns 404."""
        resp = client.post("/auth/login", json={
            "email": "nobody@example.com",
            "password": "anything",
        })
        assert resp.status_code == 404
        assert "non trouvé" in resp.json()["detail"]

    def test_login_email_case_insensitive(self, client):
        """Email matching is case-insensitive."""
        client.post("/auth/register", json={
            "email": "Henry@Example.COM",
            "pseudo": "henry",
            "last_name": "Ford",
            "first_name": "Henry",
            "phone": "0600000008",
            "password": "pw",
        })
        resp = client.post("/auth/login", json={
            "email": "henry@example.com",
            "password": "pw",
        })
        assert resp.status_code == 200
