import os
import json

import requests
import firebase_admin
from firebase_admin import auth as fb_auth, credentials
from google.cloud import secretmanager
from sqlalchemy import text
from flask import Flask, render_template, jsonify, request, session, redirect

from db import mysql_engine, mongo_db


app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-not-for-prod")

def send_audit_log(order_id: int, user_id: int, total):
    url = os.getenv("AUDIT_FUNCTION_URL")
    if not url:
        return
    try:
        requests.post(
            url,
            json={
                "order_id": order_id,
                "user_id": user_id,
                "total": float(total)},
                timeout=3
        )
    except Exception:
        pass #Never break checkout if audit fails!

def get_secret(name: str) -> str:
    project_id = os.environ["GOOGLE_CLOUD_PROJECT"]
    client = secretmanager.SecretManagerServiceClient()
    secret_path = f"projects/{project_id}/secrets/{name}/versions/latest"
    return client.access_secret_version(
        request={"name": secret_path}
    ).payload.data.decode("utf-8")
TRANSLATE_API_KEY = get_secret("TRANSLATE_API_KEY")

def is_admin() -> bool:
    admin_emails = [
        e.strip().lower()
        for e in (os.getenv("ADMIN_EMAIL") or "").split(",")
        if e.strip()
    ]
    return (session.get("email") or "").lower() in admin_emails


if not firebase_admin._apps:
    firebase_json = get_secret("FIREBASEID")
    cred = credentials.Certificate(json.loads(firebase_json))
    firebase_admin.initialize_app(cred)


# Protecting the endpoints
from functools import wraps
def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "uid" not in session:
            return jsonify({"success": False, "error": "Not logged in"}), 401
        return fn(*args, **kwargs)
    return wrapper

def page_login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "uid" not in session:
            return redirect("/login")
        return fn(*args, **kwargs)
    return wrapper

@app.context_processor
def inject_user():
    return {
        "current_user": {
            "email": session.get("email"),
            "user_id": session.get("user_id"),
            "uid": session.get("uid"),
        } if session.get("email") else None
    }

@app.context_processor
def inject_admin_flag():
    return {"is_admin": is_admin()}

#End of endpoint protection/sanitisation

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/logs")
@login_required
def get_logs():
    logs = list(mongo_db["order_logs"].find({}, {"_id": 0}))
    return jsonify(logs)

@app.route("/api/menu", methods=["GET"])
def get_menu():
    try:
        conn = mysql_engine.connect()
        result = conn.execute(text("SELECT id, name, price FROM menu"))
        menu_items = [dict(r._mapping) for r in result]
        conn.close()

        return jsonify({
            "success": True,
            "menu": menu_items
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route("/api/menu", methods=["POST"])
@login_required
def add_menu_item():
    # Admin check (comma-separated allowed emails)
    admin_emails = [e.strip().lower() for e in (os.getenv("ADMIN_EMAIL") or "").split(",") if e.strip()]
    if (session.get("email") or "").lower() not in admin_emails:
        return jsonify({"success": False, "error": "Admin only"}), 403

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    price = data.get("price")

    if not name:
        return jsonify({"success": False, "error": "Name is required"}), 400

    try:
        price = float(price)
        if price <= 0:
            raise ValueError()
    except Exception:
        return jsonify({"success": False, "error": "Price must be a positive number"}), 400

    with mysql_engine.begin() as conn:
        result = conn.execute(
            text("INSERT INTO menu (name, price) VALUES (:name, :price)"),
            {"name": name, "price": price}
        )
        new_id = result.lastrowid

    return jsonify({"success": True, "id": new_id, "name": name, "price": price})




@app.route("/api/order", methods=["POST"])
@login_required
def create_order():
    try:
        data = request.get_json()
        items = data.get("items", [])
        user_id = session.get("user_id")

        if not user_id:
            return jsonify({"success": False, "error": "User not logged in"}), 401

        if not items:
            return jsonify({"success": False, "error": "Invalid request data"}), 400

        # commits automatically on success
        with mysql_engine.begin() as conn:
            result = conn.execute(
                text("""
                    INSERT INTO orders (user_id, total, status)
                    VALUES (:user_id, 0, 'pending')
                """),
                {"user_id": user_id}
            )
            order_id = result.lastrowid

            total_price = 0.0

            for item in items:
                menu_id = item.get("menu_id")
                quantity = item.get("quantity")

                if not menu_id or not quantity or quantity <= 0:
                    continue

                price_row = conn.execute(
                    text("SELECT price FROM menu WHERE id = :menu_id"),
                    {"menu_id": menu_id}
                ).fetchone()

                if not price_row:
                    continue

                price = float(price_row[0])
                total_price += price * quantity

                conn.execute(
                    text("""
                        INSERT INTO order_items (order_id, menu_id, quantity)
                        VALUES (:order_id, :menu_id, :quantity)
                    """),
                    {
                        "order_id": order_id,
                        "menu_id": menu_id,
                        "quantity": quantity
                    }
                )

            conn.execute(
                text("UPDATE orders SET total = :total WHERE id = :order_id"),
                {"total": total_price, "order_id": order_id}
            )

        mongo_db["order_logs"].insert_one({
            "order_id": order_id,
            "user_id": user_id,
            "items": items,
            "message": "Order created"
        })

        try:
            send_audit_log(order_id, user_id, total_price)
        except Exception as audit_err:
            app.logger.exception("Audit log call failed: %s", audit_err)
        
        return jsonify({"success": True, "order_id": order_id})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/orders", methods=["GET"])
@login_required
def list_my_orders():
    try:
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"success": False, "error": "Not logged in"}), 401

        with mysql_engine.connect() as conn:
            rows = conn.execute(
                text("""
                SELECT id, total, status, created_at
                FROM orders
                WHERE user_id = :user_id
                ORDER BY created_at DESC                
                """),
                {"user_id": user_id}
            ).fetchall()
        
        orders = [dict(r._mapping) for r in rows]
        return jsonify({"success": True, "orders": orders})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/order/<int:order_id>", methods=["GET"])
@login_required
def get_order(order_id: int):
    try:
        conn = mysql_engine.connect()

        order_row = conn.execute(
            text("""
                SELECT id, user_id, total, status, created_at
                FROM orders
                WHERE id = :order_id
            """),
            {"order_id": order_id}
        ).fetchone()

        if not order_row:
            conn.close()
            return jsonify({
                "success": False,
                "error": "Order not found"
            }), 404

        if order_row._mapping["user_id"] != session.get("user_id"):
            conn.close()
            return jsonify({
                "success": False,
                "error": "Unauthorized"
            }), 403

        order = dict(order_row._mapping)

        items_result = conn.execute(
            text("""
                SELECT oi.menu_id, m.name, m.price, oi.quantity,
                       (m.price * oi.quantity) AS line_total
                FROM order_items oi
                JOIN menu m ON m.id = oi.menu_id
                WHERE oi.order_id = :order_id
            """),
            {"order_id": order_id}
        )

        items = [dict(r._mapping) for r in items_result]
        conn.close()

        logs = list(
            mongo_db["order_logs"].find({"order_id": order_id}, {"_id": 0})
        )

        return jsonify({
            "success": True,
            "order": order,
            "items": items,
            "logs": logs
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route("/api/order/<int:order_id>/status", methods=["PATCH"])
@login_required
def update_order_status(order_id: int):
    if not is_admin():
        return jsonify({"success": False, "error": "Admin only"}), 403

    data = request.get_json(silent=True) or {}
    new_status = (data.get("status") or "").strip().lower()

    allowed = {"pending", "confirmed", "completed"}
    if new_status not in allowed:
        return jsonify({"success": False, "error": f"Invalid status. Allowed: {sorted(allowed)}"}), 400

    with mysql_engine.begin() as conn:
        row = conn.execute(
            text("SELECT id, status FROM orders WHERE id = :id"),
            {"id": order_id}
        ).fetchone()

        if not row:
            return jsonify({"success": False, "error": "Order not found"}), 404

        conn.execute(
            text("UPDATE orders SET status = :status WHERE id = :id"),
            {"status": new_status, "id": order_id}
        )

    # Optional: log status change in Mongo for audit trail
        mongo_db["order_logs"].insert_one({
        "order_id": order_id,
        "user_id": session.get("user_id"),
        "message": f"Admin set status to {new_status}",
        "status": new_status
    })

    return jsonify({"success": True, "order_id": order_id, "status": new_status})

@app.route("/admin/orders")
@page_login_required
def admin_orders_page():
    if not is_admin():
        return redirect("/")
    return render_template("admin_orders.html")

@app.route("/api/admin/orders", methods=["GET"])
@login_required
def admin_list_orders():
    if not is_admin():
        return jsonify({"success": False, "error": "Admin only"}), 403

    with mysql_engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT o.id, o.user_id, u.email, o.total, o.status, o.created_at
            FROM orders o
            JOIN users u ON u.id = o.user_id
            WHERE o.hidden_from_admin = 0
            ORDER BY o.created_at DESC
        """)).fetchall()

    orders = [dict(r._mapping) for r in rows]
    return jsonify({"success": True, "orders": orders})

@app.route("/api/order/<int:order_id>/hide", methods=["PATCH"])
@login_required
def hide_order_from_admin(order_id: int):
    if not is_admin():
        return jsonify({"success": False, "error": "Admin only"}), 403

    with mysql_engine.begin() as conn:
        row = conn.execute(
            text("SELECT status, hidden_from_admin FROM orders WHERE id = :id"),
            {"id": order_id}
        ).fetchone()

        if not row:
            return jsonify({"success": False, "error": "Order not found"}), 404

        status = (row._mapping["status"] or "").lower()
        if status != "completed":
            return jsonify({"success": False, "error": "Only completed orders can be deleted from admin view"}), 400

        conn.execute(
            text("UPDATE orders SET hidden_from_admin = 1 WHERE id = :id"),
            {"id": order_id}
        )

    # optional audit trail in Mongo
    mongo_db["order_logs"].insert_one({
        "order_id": order_id,
        "user_id": session.get("user_id"),
        "message": "Admin hid order from admin view",
        "action": "hide_from_admin"
    })

    return jsonify({"success": True, "order_id": order_id})


@app.route("/api/translate", methods=["POST"])
@login_required
def translate_text():
    data = request.get_json(silent=True) or {}
    text_in = data.get("text", "")
    target = data.get("target", "es") #spanish

    if not text_in.strip():
        return jsonify({
            "success": False,
            "error": "No text to translate"
        }), 400

    Translation_Key = TRANSLATE_API_KEY.strip()

    url = f"https://translation.googleapis.com/language/translate/v2?key={Translation_Key}"
    payload = {"q": text_in, "target": target}

    r = requests.post(url, json=payload, timeout=10)
    if r.status_code != 200:
        return jsonify({"success": False, "error": r.text}), 502

    out = r.json()
    translated = out["data"]["translations"][0]["translatedText"]
    return jsonify({"success": True, "translated": translated, "target": target})

@app.route("/login")
def login_page():
    return render_template("login.html")

@app.route("/sessionLogin", methods=["POST"])
def session_login():
    try:
        data = request.get_json(silent=True) or {}
        id_token = data.get("idToken")

        if not id_token:
            return jsonify({
                "success": False,
                "error": "Missing idToken"
            }), 400

        decoded = fb_auth.verify_id_token(id_token)

        email = decoded.get("email")
        name = decoded.get("name")
        if not email:
            return jsonify({
                "success": False,
                "error": "No email in token"
            }), 400


        with mysql_engine.begin() as conn:
            row = conn.execute(text("SELECT id FROM users WHERE email = :email"), {"email": email}).fetchone()

            if row:
                user_id = row[0]
            else:
                result = conn.execute(text("INSERT INTO users (email, name) VALUES (:email, :name)"), {"email": email, "name": name})
                user_id = result.lastrowid

        session["uid"] = decoded["uid"]
        session["email"] = email
        session["user_id"] = user_id
        return jsonify({"success": True})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 401

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

@app.route("/menu")
@page_login_required
def menu_page():
    return render_template("menu.html", is_admin=is_admin())

@app.route("/orders")
@page_login_required
def orders_page():
    return render_template("orders.html")

@app.route("/translate")
@page_login_required
def translate_page():
    return render_template("translate.html")

# FOR DEVELOPMENT USE ONLY
@app.route("/test-mongo")
def test_mongo():
    try:
        logs_cursor = mongo_db["order_logs"].find({})
        logs = []

        for log in logs_cursor:
            log["_id"] = str(log["_id"])  # make JSON serialisable
            logs.append(log)

        return {
            "connected": True,
            "collections": mongo_db.list_collection_names(),
            "count": len(logs),
            "logs": logs
        }

    except Exception as e:
        return {"connected": False, "error": str(e)}, 500

@app.route("/whoami")
def whoami():
    return jsonify({
        "uid": session.get("uid"),
        "email": session.get("email"),
        "user_id": session.get("user_id")
    })

@app.route("/debug/admin")
def debug_admin():
    return {
        "session_email": session.get("email"),
        "ADMIN_EMAIL": os.getenv("ADMIN_EMAIL")
    }

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)