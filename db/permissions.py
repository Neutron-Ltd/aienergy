import sys
from pathlib import Path

import mysql.connector

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
from db.mysql_env import mysql_connect_kwargs

config = mysql_connect_kwargs()

try:
    conn = mysql.connector.connect(**config)
    cursor = conn.cursor()

    # 1. Get the Current User (to be sure who we are logged in as)
    cursor.execute("SELECT current_user();")
    user = cursor.fetchone()[0]
    print(f"Logged in as: {user}\n")

    # 2. Show Grants (The specific permissions list)
    print("--- RAW PERMISSIONS ---")
    cursor.execute("SHOW GRANTS FOR CURRENT_USER();")
    
    grants = cursor.fetchall()
    for row in grants:
        # The permission string is always in the first column
        print(row[0])

except mysql.connector.Error as err:
    print(f"Error: {err}")

finally:
    if 'conn' in locals() and conn.is_connected():
        cursor.close()
        conn.close()