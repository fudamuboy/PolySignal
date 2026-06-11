from app.database import Database
import logging

logging.basicConfig(level=logging.INFO)
db = Database()
db.clear_all_positions()

# Clear trades table
with db._get_connection() as conn:
    cursor = conn.cursor()
    cursor.execute("DELETE FROM trades;")
    conn.commit()

print("Database cleared for live test!")
