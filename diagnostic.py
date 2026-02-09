import mysql.connector
import pandas as pd
import warnings

# Suppress warnings
warnings.filterwarnings('ignore')

# --- CONFIGURATION ---
TARGET_DATE = "260202"      # Feb 2nd, 2026
TABLE_NAME = f"site_power_{TARGET_DATE}"

db_config = {
    'user': 'energy_user',
    'password': 'REDACTED',
    'host': 'localhost',
    'port': 3307,
    'database': 'unified'
}

def run_diagnostic():
    print(f"--- 🔍 Diagnostic for {TABLE_NAME} ---")
    
    try:
        conn = mysql.connector.connect(**db_config)
    except mysql.connector.Error as err:
        print(f"Error connecting: {err}")
        return

    # 1. Find a valid meter (Don't guess ID 102, find one that actually has data)
    print("Finding an active meter to analyze...")
    try:
        query_find = f"SELECT DISTINCT meter_number FROM {TABLE_NAME} LIMIT 1"
        cursor = conn.cursor()
        cursor.execute(query_find)
        result = cursor.fetchone()
        
        if not result:
            print("CRITICAL: The table is empty! No data found for this date.")
            return
            
        target_meter_id = result[0]
        print(f"-> Selected Meter ID: {target_meter_id} for analysis.\n")
        
    except Exception as e:
        print(f"Error querying table: {e}")
        return

    # 2. Get Timestamps for this meter
    print(f"Fetching timestamps for Meter {target_meter_id}...")
    query_ts = f"SELECT ts FROM {TABLE_NAME} WHERE meter_number = {target_meter_id} ORDER BY ts ASC"
    df = pd.read_sql(query_ts, conn)
    conn.close()

    if df.empty or len(df) < 2:
        print("Not enough data points to calculate frequency.")
        return

    # 3. Calculate Intervals (The "Delta")
    df['ts'] = pd.to_datetime(df['ts'])
    df['delta'] = df['ts'].diff()

    # 4. Statistical Analysis
    # Mode = The most common time difference (The "Rhythm")
    most_common = df['delta'].mode()[0]
    
    # Min/Max/Mean
    min_gap = df['delta'].min()
    max_gap = df['delta'].max()
    avg_gap = df['delta'].mean()

    # 5. Print Report
    print("-" * 40)
    print(f"📊 REPORTING RHYTHM FOR METER {target_meter_id}")
    print("-" * 40)
    print(f"Total Rows Found:   {len(df)}")
    print(f"First Timestamp:    {df['ts'].min()}")
    print(f"Last Timestamp:     {df['ts'].max()}")
    print("-" * 40)
    print(f"✅ MOST COMMON INTERVAL: {most_common}  <-- THIS IS YOUR REAL FREQUENCY")
    print(f"   Minimum Gap:          {min_gap}")
    print(f"   Maximum Gap:          {max_gap}")
    print(f"   Average Gap:          {avg_gap}")
    print("-" * 40)
    
    print("\nFirst 10 Actual Intervals (Time between rows):")
    print(df['delta'].head(10).to_string(index=False))

    print("\n" + "="*50)
    print("RECOMMENDATION:")
    
    # Logic to give advice
    seconds = most_common.total_seconds()
    if 290 <= seconds <= 310:
        print("Your meters report every ~5 MINUTES.")
        print("Set EXPECTED_FREQ = '5min' in your main script.")
    elif 890 <= seconds <= 910:
        print("Your meters report every ~15 MINUTES.")
        print("Set EXPECTED_FREQ = '15min' in your main script.")
    elif 3590 <= seconds <= 3610:
        print("Your meters report every ~1 HOUR.")
        print("Set EXPECTED_FREQ = '60min' in your main script.")
    else:
        print(f"Your meters have an irregular rhythm of {most_common}.")
        print("You should use the 'Adaptive' logic instead of a fixed frequency.")
    print("="*50)

if __name__ == "__main__":
    run_diagnostic()