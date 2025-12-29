import os
from flask import Flask, render_template, jsonify, request, session, redirect, make_response
import firebase_admin
from firebase_admin import auth as fb_auth, credentials
from db import mysql_engine, mongo_db
from sqlalchemy import text
from google.cloud import secretmanager
import json
import requests

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-not-for-prod")

def send_audit_log(order_id: int, user_id: int, total):
    url = os.getenv("AUDIT_LOG_URL")
    if not url:
        return

    try:
        requests.post(
            url,
            json={
                "order_id": order_id,
                "user_id": user,
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


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/logs")
@login_required
def get_logs():
    logs = list(mongo_db["order_logs"].find({}, {"_id": 0}))
    return jsonify(logs)


# test
@app.route("/test-mongo")
def test_mongo():
    try:
        collections = mongo_db.list_collection_names()
        first_log = mongo_db["order_logs"].find_one({}, {"_id": 0})

        return {
            "connected": True,
            "collections": collections,
            "sample_log": first_log,
        }
    except Exception as e:
        return {"connected": False, "error": str(e)}
# end test


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


@app.route("/whoami")
def whoami():
    return jsonify({
        "uid": session.get("uid"),
        "email": session.get("email"),
        "user_id": session.get("user_id")
    })


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
