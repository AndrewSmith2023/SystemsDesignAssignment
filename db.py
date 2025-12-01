from sqlalchemy import create_engine
from pymongo import MongoClient
from config import DB_USER, DB_PASS, DB_NAME, INSTANCE_CONNECTION_NAME, MONGO_URI

# MySQL (Cloud SQL)
mysql_engine = create_engine(
    f"mysql+pymysql://{DB_USER}:{DB_PASS}@/{DB_NAME}"
    f"?unix_socket=/cloudsql/{INSTANCE_CONNECTION_NAME}"
)

# MongoDB (Atlas)
mongo_client = MongoClient(MONGO_URI)
mongo_db = mongo_client["restaurant_app"]
