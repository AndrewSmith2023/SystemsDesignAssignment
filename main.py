from flask import Flask, render_template, jsonify
from db import mysql_engine, mongo_db

app = Flask(__name__)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/menu")
def get_menu():
    conn = mysql_engine.connect()
    result = conn.execute("SELECT id, name, price FROM menu")
    menu = [dict(row) for row in result]
    conn.close()
    return jsonify(menu)

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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
