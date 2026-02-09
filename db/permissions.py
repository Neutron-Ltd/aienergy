import mysql.connector

# Use your existing config
config = {
    'user': 'energy_user',
    'password': 'REDACTED',
    'host': 'localhost',
    'port': 3307,
    'database': 'unified'
}

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