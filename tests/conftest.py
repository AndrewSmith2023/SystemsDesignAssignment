import os
import sys
import importlib
import types
import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

@pytest.fixture
def app_module(monkeypatch):
    monkeypatch.setenv("FLASK_SECRET_KEY", "test")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("ADMIN_EMAIL", "admin@example.com")

    for m in ["firebase_admin", "firebase_admin.auth", "firebase_admin.credentials"]:
        sys.modules.pop(m, None)

    fake_firebase_admin = types.ModuleType("firebase_admin")
    fake_firebase_admin._apps = [object()]  # makes "if not firebase_admin._apps" false

    fake_fb_auth = types.ModuleType("firebase_admin.auth")
    fake_fb_credentials = types.ModuleType("firebase_admin.credentials")

    fake_firebase_admin.auth = fake_fb_auth
    fake_firebase_admin.credentials = fake_fb_credentials

    sys.modules["firebase_admin"] = fake_firebase_admin
    sys.modules["firebase_admin.auth"] = fake_fb_auth
    sys.modules["firebase_admin.credentials"] = fake_fb_credentials

    for m in ["google", "google.cloud", "google.cloud.secretmanager"]:
        sys.modules.pop(m, None)

    fake_secretmanager = types.ModuleType("google.cloud.secretmanager")

    class FakePayload:
        def __init__(self, s: str):
            self.data = s.encode("utf-8")

    class FakeAccessResp:
        def __init__(self, s: str):
            self.payload = FakePayload(s)

    class FakeSecretManagerClient:
        def access_secret_version(self, *args, **kwargs):
            request = kwargs.get("request")
            name = kwargs.get("name")
            if request and isinstance(request, dict):
                name = request.get("name", name)
            name = name or ""
            # Return harmless strings
            if "TRANSLATE_API_KEY" in name:
                return FakeAccessResp("fake-translate-key")
            if "FIREBASEID" in name:
                return FakeAccessResp("fake")  
            return FakeAccessResp("fake")

    fake_secretmanager.SecretManagerServiceClient = FakeSecretManagerClient

    google_mod = types.ModuleType("google")
    cloud_mod = types.ModuleType("google.cloud")

    sys.modules["google"] = google_mod
    sys.modules["google.cloud"] = cloud_mod
    sys.modules["google.cloud.secretmanager"] = fake_secretmanager

    import main
    importlib.reload(main)

    main.app.config["TESTING"] = True
    return main

@pytest.fixture
def client(app_module):
    return app_module.app.test_client()

def login_session(client, email="user@example.com", user_id=1, uid="abc"):
    with client.session_transaction() as sess:
        sess["email"] = email
        sess["user_id"] = user_id
        sess["uid"] = uid
