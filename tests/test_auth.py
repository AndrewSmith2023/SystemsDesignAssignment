from tests.conftest import login_session

def test_logs_requires_login(client):
    res = client.get("/api/logs")
    assert res.status_code == 401
    assert res.get_json()["success"] is False

def test_add_menu_requires_admin(client):
    login_session(client, email="user@example.com")
    res = client.post("/api/menu", json={"name": "Pizza", "price": 10})
    assert res.status_code == 403
    assert res.get_json()["error"] == "Admin only"

def test_translate_empty_text_400(client):
    login_session(client)
    res = client.post("/api/translate", json={"text": "   ", "target": "es"})
    assert res.status_code == 400
    assert res.get_json()["success"] is False
