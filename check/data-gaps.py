import mysql.connector
import pandas as pd
import warnings

# Suppress warnings
warnings.filterwarnings('ignore')

# --- CONFIGURATION ---
TARGET_DATE = "260202"   # <--- Updated to Feb 2nd, 2026
EXPECTED_FREQ = '5min'   # Adjust this if your meters report every 15 mins
# ---------------------

db_config = {
    'user': 'energy_user',
    'password': 'REDACTED',
    'host': 'localhost',
    'port': 3307,
    'database': 'unified'
}

def generate_report():
    print(f"--- Starting Gap Analysis for Date: {TARGET_DATE} ---")
    
    try:
        conn = mysql.connector.connect(**db_config)
    except mysql.connector.Error as err:
        print(f"Error connecting to DB: {err}")
        return

    # 1. Get List of All Meters
    print("Fetching meter list...")
    try:
        meters_df = pd.read_sql("SELECT meter_number, meter_name FROM meter_info", conn)
        total_meters = len(meters_df)
        print(f"Found {total_meters} meters in the system registry.\n")
    except Exception as e:
        print(f"Error fetching meter_info: {e}")
        return

    # 2. Get All Data for the Day
    table_name = f"site_power_{TARGET_DATE}"
    print(f"Downloading timestamps from {table_name}...")
    
    try:
        query = f"SELECT meter_number, ts FROM {table_name}"
        all_data = pd.read_sql(query, conn)
    except Exception as e:
        print(f"\nCRITICAL ERROR: Could not read table '{table_name}'.")
        print(f"Reason: {e}")
        print("Note: If this error says 'Table doesn't exist', then no data was logged on this date.")
        conn.close()
        return

    conn.close()

    if all_data.empty:
        print("Table is empty! No data recorded for this entire day.")
        return

    # Convert timestamps
    all_data['ts'] = pd.to_datetime(all_data['ts'])

    # 3. Analyze Each Meter
    print("Analyzing data continuity...")
    results = []
    
    # Group data by meter for fast lookup
    grouped = all_data.groupby('meter_number')
    meters_with_data = set(grouped.groups.keys())

    for index, row in meters_df.iterrows():
        m_id = row['meter_number']
        m_name = row['meter_name']
        
        # STATUS 1: DEAD (No data at all for this ID in today's table)
        if m_id not in meters_with_data:
            results.append({
                'meter_id': m_id,
                'name': m_name,
                'status': 'DEAD / NO DATA',
                'gap_count': -1
            })
            continue

        # STATUS 2: GAP ANALYSIS
        meter_ts = grouped.get_group(m_id).sort_values('ts').set_index('ts')
        
        # Create a "perfect" timeline for the full 24 hours of that day
        # This ensures we catch meters that started late (e.g., 10 AM) or stopped early (e.g., 8 PM)
        day_start = pd.Timestamp(f"20{TARGET_DATE}").replace(hour=0, minute=0, second=0)
        day_end   = pd.Timestamp(f"20{TARGET_DATE}").replace(hour=23, minute=59, second=59)
        
        # We reindex against this full day range
        try:
            # Create the perfect index (every 5 mins)
            full_index = pd.date_range(start=day_start, end=day_end, freq=EXPECTED_FREQ)
            
            # Reindex the actual data to match the perfect index
            # This inserts NaNs where data is missing
            reindexed_data = meter_ts.reindex(full_index)
            
            # Count the NaNs
            missing_count = reindexed_data.index.difference(meter_ts.index).shape[0]
            
            status = 'PERFECT' if missing_count == 0 else 'HAS GAPS'
            
            results.append({
                'meter_id': m_id,
                'name': m_name,
                'status': status,
                'gap_count': missing_count
            })
            
        except Exception as e:
            results.append({'meter_id': m_id, 'name': m_name, 'status': f'ERROR: {e}', 'gap_count': 0})

    # 4. Generate CSV Report
    report_df = pd.DataFrame(results)
    report_df = report_df.sort_values(by=['status', 'gap_count'], ascending=[True, False])
    
    filename = f"gap_report_{TARGET_DATE}.csv"
    report_df.to_csv(filename, index=False)
    
    print("\n" + "="*40)
    print(f"ANALYSIS COMPLETE for 20{TARGET_DATE}")
    print("="*40)
    print(f"Total Meters: {total_meters}")
    print(f"Perfect Meters: {len(report_df[report_df['status']=='PERFECT'])}")
    print(f"Meters with Gaps: {len(report_df[report_df['status']=='HAS GAPS'])}")
    print(f"Dead Meters: {len(report_df[report_df['status']=='DEAD / NO DATA'])}")
    print("="*40)
    print(f"Report saved to: {filename}")

if __name__ == "__main__":
    generate_report()