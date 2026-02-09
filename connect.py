import mysql.connector

# 1. Configuration (Based on your screenshots)
config = {
    'user': 'energy_user',
    'password': 'REDACTED',
    'host': 'localhost',
    'port': 3307,
    'database': 'unified'
}

try:
    # 2. Establish Connection
    print("Connecting to database...")
    conn = mysql.connector.connect(**config)
    cursor = conn.cursor()
    print("Success! Connected to 'unified'.")

    # 3. Run a Simple Query (Check Meter Info)
    query = "SELECT id, meter_name, mac_address FROM meter_info LIMIT 5;"
    cursor.execute(query)

    # 4. Fetch and Print Results
    rows = cursor.fetchall()
    print("\n--- First 5 Meters ---")
    for row in rows:
        print(row)

except mysql.connector.Error as err:
    print(f"Error: {err}")

finally:
    # 5. Clean up
    if 'conn' in locals() and conn.is_connected():
        cursor.close()
        conn.close()
        print("\nConnection closed.")