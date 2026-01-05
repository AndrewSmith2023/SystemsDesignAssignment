from tests.conftest import login_session

def test_update_status_rejects_invalid_status(client):
    login_session(client, email="admin@example.com")  # admin for role check
    res = client.patch("/api/order/1/status", json={"status": "hacked"})
    assert res.status_code == 400
    assert res.get_json()["success"] is False
