from flask import Flask, render_template, jsonify, request
from db import mysql_engine, mongo_db
from sqlalchemy import text

app = Flask(__name__)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/logs")
def get_logs():
    logs = list(mongo_db["order_logs"].find({}, {"_id": 0}))
    return jsonify(logs)

#test
@app.route("/test-mongo")
def test_mongo():
    try:
        # Get collections
        collections = mongo_db.list_collection_names()

        # Try reading one document from order_logs
        first_log = mongo_db["order_logs"].find_one({}, {"_id": 0})

        return {
            "connected": True,
            "collections": collections,
            "sample_log": first_log
        }
    except Exception as e:
        return {"connected": False, "error": str(e)}
#end test

@app.route("/api/menu", methods=["GET"])
def get_menu():
    try:
        conn = mysql_engine.connect()
        result = conn.execute(text("SELECT id, name, price FROM menu"))
        menu_items = [dict(row) for row in result]
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
def create_order():
    try:
        data = request.get_json()
        user_id = data.get("user_id")
        items = data.get("items", [])

        if not user_id or not items:
            return jsonify({
                "success": False,
                "error": "Invalid request data"
            }), 400
        
        conn = mysql_engine.connect()
        
        insert_order = text("""
                INSERT INTO orders (user_id, total, status)
                VALUES (:user_id, 0, 'pending')
        """)

        result = conn.execute(insert_order, {"user_id": user_id})
        order_id = result.lastrowid

        total_price = 0
        for item in items:
            menu_id = item["menu_id"]
            quantity = item["quantity"]

            price_query = text("SELECT price FROM menu WHERE id = :menu_id")
            price_row = conn.execute(price_query, {"menu_id": menu_id}).fetchone()

            if not price_row:
                continue

            price = float(price_row[0])
            total_price += price * quantity

            insert_item = text("""
                INSERT INTO order_items (order_id, menu_id, quantity)
                VALUES (:order_id, :menu_id, :quantity)
            """)
            conn.execute(insert_item, {
                "order_id": order_id,
                "menu_id": menu_id,
                "quantity": quantity
            })

        update_total = text("""
            UPDATE orders SET total = :total WHERE id = :order_id
        """)
        conn.execute(update_total, {"total": total_price, "order_id": order_id})

        conn.close

        mongo_db["order_logs"].insert_one({
            "order_id": order_id,
            "user_id": user_id,
            "items": items,
        })

        return jsonify({
            "success": True,
            "order_id": order_id
        })


    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
