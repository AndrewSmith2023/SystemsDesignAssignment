from tests.conftest import login_session

def test_translate_success_mocked(client, monkeypatch, app_module):
    login_session(client)

    class FakeResp:
        status_code = 200
        def json(self):
            return {"data": {"translations": [{"translatedText": "hola"}]}}

    monkeypatch.setattr(app_module.requests, "post", lambda *a, **k: FakeResp())
    monkeypatch.setattr(app_module, "get_translate_key", lambda: "fake-key")

    res = client.post("/api/translate", json={"text": "hello", "target": "es"})
    assert res.status_code == 200
    assert res.get_json()["success"] is True
    assert res.get_json()["translated"] == "hola"
