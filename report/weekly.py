import mysql.connector
import pandas as pd
import warnings
import webbrowser
import os
from datetime import datetime, timedelta

# Suppress warnings
warnings.filterwarnings('ignore')

# --- CONFIGURATION ---
START_DATE_STR = "2026-02-01"   # YYYY-MM-DD (Monday)
DAYS_TO_ANALYZE = 7             # 1 Week
EXPECTED_DAILY_COUNT = 96       # 15 min intervals
EXCLUDED_METER_IDS = {1}        # 1 = 电网计量表 (main grid input table, not a meter)
# Meter IDs for special handling (excluded from facility consumption sum)
GRID_METER_ID = 1
SOLAR_METER_IDS = {29, 31, 56}  # PV generation meters
STORAGE_METER_ID = 30           # Energy storage
# ---------------------

db_config = {
    'user': 'energy_user',
    'password': 'REDACTED',
    'host': 'localhost',
    'port': 3307,
    'database': 'unified'
}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
# Filename: weekly-YYMMDD.html (e.g. weekly-260201.html)
START_DATE_SUFFIX = datetime.strptime(START_DATE_STR, "%Y-%m-%d").strftime("%y%m%d")
REPORT_FILE = os.path.join(DATA_DIR, f"weekly-{START_DATE_SUFFIX}.html")

# --- HTML TEMPLATE ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Weekly Meter Report: {start_date}</title>
    <style>
        body {{ font-family: 'Segoe UI', sans-serif; background-color: #f4f6f9; padding: 20px; color: #333; }}
        .container {{ max-width: 1400px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 4px 15px rgba(0,0,0,0.05); }}
        
        h1 {{ color: #2c3e50; border-bottom: 2px solid #eee; padding-bottom: 10px; }}
        .meta {{ color: #7f8c8d; font-size: 14px; margin-bottom: 30px; }}

        /* Summary Cards */
        .summary {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin-bottom: 30px; }}
        .card {{ background: #fff; border: 1px solid #ddd; padding: 15px; border-radius: 6px; text-align: center; }}
        .card h3 {{ margin: 0; font-size: 28px; color: #2c3e50; }}
        .card p {{ margin: 5px 0 0; font-size: 12px; color: #95a5a6; font-weight: bold; text-transform: uppercase; }}

        /* Table */
        table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
        th {{ background: #f8f9fa; padding: 12px; text-align: left; border-bottom: 2px solid #dee2e6; color: #2c3e50; }}
        td {{ padding: 10px; border-bottom: 1px solid #eee; vertical-align: middle; }}
        tr:hover {{ background-color: #f8f9fa; }}

        /* Heatmap Grid */
        .week-grid {{ display: flex; gap: 4px; }}
        .day-box {{ width: 30px; height: 30px; border-radius: 3px; font-size: 10px; display: flex; align-items: center; justify-content: center; color: white; cursor: help; font-weight: bold; }}
        
        .s-perfect {{ background-color: #2ecc71; }}  /* Green */
        .s-good {{ background-color: #f1c40f; color: #7f8c8d; }}     /* Yellow */
        .s-bad {{ background-color: #e74c3c; }}      /* Red */
        .s-empty {{ background-color: #ecf0f1; border: 1px solid #bdc3c7; color: #bdc3c7; }} /* Grey */

        .badge {{ padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 11px; }}
        .bg-green {{ background: #d5f5e3; color: #1eb866; }}
        .bg-red {{ background: #fadbd8; color: #e74c3c; }}

        .energy-comparison {{ margin-top: 30px; padding: 20px; background: #fff; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.05); }}
        .energy-comparison h2 {{ margin-top: 0; color: #2c3e50; padding-bottom: 10px; border-bottom: 2px solid #eee; }}
        .energy-grid {{ display: grid; grid-template-columns: repeat(7, 1fr); gap: 15px; margin-top: 20px; }}
        .energy-card {{ background: #f8f9fa; border: 1px solid #e9ecef; padding: 20px 10px; border-radius: 8px; text-align: center; display: flex; flex-direction: column; justify-content: center; }}
        .energy-card h3 {{ margin: 0; font-size: 20px; color: #2c3e50; font-weight: 700; }}
        .energy-card p {{ margin: 8px 0 0; color: #7f8c8d; font-size: 11px; font-weight: 700; text-transform: uppercase; line-height: 1.3; }}
        .energy-card.supply {{ border-top: 4px solid #3498db; }}
        .energy-card.accounted {{ border-top: 4px solid #2ecc71; }}
        .energy-card.loss {{ border-top: 4px solid #e74c3c; background-color: #fdf2f0; }}
        .energy-card.loss h3 {{ color: #c0392b; }}
        .math-sign {{ display: flex; align-items: center; justify-content: center; font-size: 24px; color: #bdc3c7; font-weight: bold; }}
        .energy-unavailable {{ color: #95a5a6; font-style: italic; padding: 15px; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📅 Weekly Meter Health Report</h1>
        <div class="meta">Period: <strong>{start_date}</strong> to <strong>{end_date}</strong> (Target: {target_weekly} records)</div>

        {energy_comparison_section}

        <div class="summary">
            <div class="card"><h3>{total_meters}</h3><p>Meters Monitored</p></div>
            <div class="card"><h3>{avg_uptime}%</h3><p>Network Avg Uptime</p></div>
            <div class="card"><h3>{perfect_meters}</h3><p>100% Reliable Devices</p></div>
            <div class="card" style="border-left: 5px solid #e74c3c"><h3>{dead_meters}</h3><p>Dead / Offline</p></div>
        </div>

        <table>
            <thead>
                <tr>
                    <th>ID</th>
                    <th>Name</th>
                    <th>Weekly Uptime</th>
                    <th>Total Records</th>
                    <th>7-Day Trend (Mon &rarr; Sun)</th>
                </tr>
            </thead>
            <tbody>
                {rows}
            </tbody>
        </table>
    </div>
</body>
</html>
"""

def fetch_weekly_data(start_date, meters_df):
    """
    Loops through 7 days, queries each daily table, and aggregates counts.
    """
    weekly_data = {} # {meter_id: {date_str: count, ...}}
    
    # Initialize structure for all meters known in registry
    for mid in meters_df['meter_number']:
        weekly_data[mid] = {}

    conn = mysql.connector.connect(**db_config)
    cursor = conn.cursor()

    # Loop through each day
    for i in range(DAYS_TO_ANALYZE):
        current_date = start_date + timedelta(days=i)
        date_str = current_date.strftime("%Y-%m-%d")
        
        # Format table name: site_power_YYMMDD
        table_suffix = current_date.strftime("%y%m%d")
        table_name = f"site_power_{table_suffix}"
        
        print(f"[{i+1}/{DAYS_TO_ANALYZE}] Querying {table_name}...")

        try:
            # Optimized Query
            query = f"""
                SELECT meter_number, COUNT(*) 
                FROM {table_name} 
                WHERE ts >= '{date_str} 00:00:00' 
                AND ts <= '{date_str} 23:59:59'
                GROUP BY meter_number
            """
            cursor.execute(query)
            results = cursor.fetchall()
            
            # Map results to data structure
            for mid, count in results:
                # FIXED: Check if meter exists in our registry
                if mid in weekly_data:
                    weekly_data[mid][date_str] = count
                else:
                    # Optional: Print warning, but don't crash
                    # print(f"   Note: Found unregistered Meter ID {mid} in logs. Skipping.")
                    pass
            
            # Mark table as "Checked" by setting 0 for missing data (instead of None)
            # If table existed, anyone who didn't report gets a 0
            for mid in weekly_data:
                if date_str not in weekly_data[mid]:
                     weekly_data[mid][date_str] = 0

        except mysql.connector.Error as err:
            print(f"   ⚠️  Skipping {table_name} (Table does not exist)")
            # Leave as None to indicate "System Error/No Table"
            pass

    conn.close()
    return weekly_data

def _integrate_power_to_kwh(rows):
    """
    Integrate power (Pt, kW) over time. rows = list of dicts with meter_number, ts, Pt.
    Returns (solar_kwh, storage_charge, storage_discharge).
    """
    solar_kwh = 0.0
    storage_charge = 0.0
    storage_discharge = 0.0
    prev = {}  # mid -> {ts, Pt}

    for row in rows:
        mid = row['meter_number']
        ts = row['ts']
        pt = row['Pt'] if row.get('Pt') is not None else 0.0

        if mid in prev:
            delta_seconds = (ts - prev[mid]['ts']).total_seconds()
            if delta_seconds > 0:
                interval_hours = delta_seconds / 3600.0
                interval_kwh = prev[mid]['Pt'] * interval_hours

                if mid in SOLAR_METER_IDS:
                    solar_kwh += abs(interval_kwh)
                elif mid == STORAGE_METER_ID:
                    if prev[mid]['Pt'] > 0:
                        storage_charge += interval_kwh
                    else:
                        storage_discharge += abs(interval_kwh)

        prev[mid] = {'ts': ts, 'Pt': pt}

    return solar_kwh, storage_charge, storage_discharge


def fetch_weekly_energy(start_date, meters_df):
    """
    Fetch energy metrics for the week.
    - Grid & Facility: from site_energy_* (ImpEP/ExpEP)
    - Solar (29,31,56) & Storage (30): from site_power_* via Pt integration (energy registers broken)
    Returns dict: grid_import, grid_export, solar_kwh, storage_charge, storage_discharge, facility_consumption
    """
    conn = mysql.connector.connect(**db_config)
    cursor = conn.cursor(dictionary=True)

    try:
        meter_info_df = pd.read_sql(
            "SELECT meter_number, parent_meter_number FROM meter_info",
            conn
        )
    except Exception:
        conn.close()
        return None

    parent_numbers = set(
        int(x) for x in meter_info_df['parent_meter_number'].dropna().astype(int).tolist()
        if x != 0
    )
    all_meter_numbers = set(meter_info_df['meter_number'].tolist())
    terminal_meter_ids = all_meter_numbers - parent_numbers
    facility_meter_ids = terminal_meter_ids - {GRID_METER_ID} - SOLAR_METER_IDS - {STORAGE_METER_ID}

    result = {
        'grid_import': 0.0,
        'grid_export': 0.0,
        'solar_kwh': 0.0,
        'storage_charge': 0.0,
        'storage_discharge': 0.0,
        'facility_consumption': 0.0,
    }
    found_any = False

    # --- 1. site_energy_* for Grid and Facility (exclude solar/storage; their registers are broken) ---
    for i in range(DAYS_TO_ANALYZE):
        current_date = start_date + timedelta(days=i)
        date_str = current_date.strftime("%Y-%m-%d")
        table_suffix = current_date.strftime("%y%m%d")
        table_name = f"site_energy_{table_suffix}"

        try:
            query = f"""
                SELECT
                    meter_number,
                    MIN(ImpEP) AS min_imp,
                    MAX(ImpEP) AS max_imp,
                    MIN(ExpEP) AS min_exp,
                    MAX(ExpEP) AS max_exp
                FROM {table_name}
                WHERE ts >= %s AND ts <= %s
                GROUP BY meter_number
            """
            cursor.execute(query, (f"{date_str} 00:00:00", f"{date_str} 23:59:59"))
            rows = cursor.fetchall()

            for row in rows:
                mid = row['meter_number']
                delta_imp = (row['max_imp'] or 0) - (row['min_imp'] or 0)
                delta_exp = (row['max_exp'] or 0) - (row['min_exp'] or 0)

                if mid == GRID_METER_ID:
                    result['grid_import'] += delta_imp
                    result['grid_export'] += delta_exp
                    found_any = True
                elif mid in facility_meter_ids:
                    result['facility_consumption'] += delta_imp
                    found_any = True
                # Skip solar/storage: use power integration instead
        except mysql.connector.Error:
            pass

    # --- 2. site_power_* integration for Solar (29,31,56) and Storage (30) ---
    power_meter_ids = tuple(SOLAR_METER_IDS | {STORAGE_METER_ID})
    in_placeholders = ",".join(["%s"] * len(power_meter_ids))

    for i in range(DAYS_TO_ANALYZE):
        current_date = start_date + timedelta(days=i)
        date_str = current_date.strftime("%Y-%m-%d")
        table_suffix = current_date.strftime("%y%m%d")
        power_table = f"site_power_{table_suffix}"

        try:
            query = f"""
                SELECT meter_number, ts, Pt
                FROM {power_table}
                WHERE meter_number IN ({in_placeholders})
                  AND ts >= %s AND ts <= %s
                ORDER BY meter_number, ts ASC
            """
            cursor.execute(query, (*power_meter_ids, f"{date_str} 00:00:00", f"{date_str} 23:59:59"))
            rows = cursor.fetchall()

            if rows:
                s, c, d = _integrate_power_to_kwh(rows)
                result['solar_kwh'] += s
                result['storage_charge'] += c
                result['storage_discharge'] += d
                found_any = True
        except mysql.connector.Error:
            pass

    conn.close()
    if not found_any:
        return None
    for k in result:
        result[k] = round(result[k], 2)
    return result

def generate_report():
    print(f"--- Starting Weekly Report for {START_DATE_STR} ---")
    
    start_dt = datetime.strptime(START_DATE_STR, "%Y-%m-%d")
    end_dt = start_dt + timedelta(days=DAYS_TO_ANALYZE-1)
    
    # 1. Get Meter List
    try:
        conn = mysql.connector.connect(**db_config)
        meters_df = pd.read_sql("SELECT meter_number, meter_name FROM meter_info", conn)
        conn.close()
    except Exception as e:
        print(f"DB Error: {e}")
        return

    # Exclude non-meter entries (e.g. main grid input table)
    meters_df = meters_df[~meters_df['meter_number'].isin(EXCLUDED_METER_IDS)]

    # 2. Get Data
    weekly_data = fetch_weekly_data(start_dt, meters_df)

    # 2b. Energy comparison (grid, solar, storage, facility - aggregated over week)
    energy_data = fetch_weekly_energy(start_dt, meters_df)

    # 3. Build Rows
    table_rows = ""
    total_meters = len(meters_df)
    perfect_meters = 0
    dead_meters = 0
    sum_uptime = 0

    target_weekly = EXPECTED_DAILY_COUNT * DAYS_TO_ANALYZE

    # Generate list of date strings for sorting columns
    date_keys = [(start_dt + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(DAYS_TO_ANALYZE)]

    print("Generating HTML...")

    # Sort meters by ID for cleaner list
    meters_df = meters_df.sort_values('meter_number')

    for index, row in meters_df.iterrows():
        mid = row['meter_number']
        m_name = row['meter_name']
        
        daily_counts = weekly_data.get(mid, {})
        
        total_rec = 0
        heatmap_html = "<div class='week-grid'>"
        
        for d_str in date_keys:
            count = daily_counts.get(d_str, None)
            
            # Determine Color Box
            if count is None:
                # Table didn't exist or script failed to read it
                css_class = "s-empty"
                tooltip = f"{d_str}: No Table/Data"
                display_txt = "X"
            else:
                total_rec += count
                display_txt = str(count)
                tooltip = f"{d_str}: {count} records"
                
                if count >= (EXPECTED_DAILY_COUNT * 0.95): css_class = "s-perfect"
                elif count >= (EXPECTED_DAILY_COUNT * 0.5): css_class = "s-good"
                else: css_class = "s-bad"
            
            heatmap_html += f"<div class='day-box {css_class}' title='{tooltip}'>{display_txt}</div>"
        
        heatmap_html += "</div>"

        # Weekly Stats
        # Avoid division by zero if all tables are missing
        if target_weekly > 0:
            uptime = (total_rec / target_weekly) * 100
        else:
            uptime = 0

        if uptime > 100: uptime = 100
        sum_uptime += uptime

        if uptime >= 98: perfect_meters += 1
        if uptime == 0: dead_meters += 1
        
        # Badge
        if uptime > 90: badge = f"<span class='badge bg-green'>{round(uptime,1)}%</span>"
        else: badge = f"<span class='badge bg-red'>{round(uptime,1)}%</span>"

        table_rows += f"""
        <tr>
            <td><b>{mid}</b></td>
            <td>{m_name}</td>
            <td>{badge}</td>
            <td><b>{total_rec}</b> / {target_weekly}</td>
            <td>{heatmap_html}</td>
        </tr>
        """

    avg_uptime = round(sum_uptime / total_meters, 1) if total_meters > 0 else 0

    # Energy comparison section – discrepancy (loss/unaccounted)
    if energy_data is not None:
        d = energy_data
        total_supply = d['grid_import'] + d['solar_kwh'] + d['storage_discharge']
        total_accounted = d['facility_consumption'] + d['storage_charge'] + d['grid_export']
        discrepancy = total_supply - total_accounted
        loss_pct = (discrepancy / total_supply * 100) if total_supply > 0 else 0

        # Dynamically build the supply cards
        supply_cards = f"""
            <div class="energy-card supply">
                <h3>{d['grid_import']:,.1f}</h3>
                <p>Grid Import<br>(Supply)</p>
            </div>
            <div class="math-sign">+</div>
            <div class="energy-card supply">
                <h3>{d['solar_kwh']:,.1f}</h3>
                <p>Solar Gen<br>(Supply)</p>
            </div>
        """
        if d['storage_discharge'] > 0:
            supply_cards += f"""
            <div class="math-sign">+</div>
            <div class="energy-card supply">
                <h3>{d['storage_discharge']:,.1f}</h3>
                <p>Battery Discharge<br>(Supply)</p>
            </div>
            """

        # Dynamically build the accounted cards
        accounted_cards = f"""
            <div class="energy-card accounted">
                <h3>{d['facility_consumption']:,.1f}</h3>
                <p>Facility Load<br>(Accounted)</p>
            </div>
        """
        if d['storage_charge'] > 0:
            accounted_cards += f"""
            <div class="math-sign">+</div>
            <div class="energy-card accounted">
                <h3>{d['storage_charge']:,.1f}</h3>
                <p>Battery Charge<br>(Accounted)</p>
            </div>
            """
        if d['grid_export'] > 0:
            accounted_cards += f"""
            <div class="math-sign">+</div>
            <div class="energy-card accounted">
                <h3>{d['grid_export']:,.1f}</h3>
                <p>Grid Export<br>(Accounted)</p>
            </div>
            """

        energy_comparison_section = f"""
        <div class="energy-comparison">
            <h2>⚡ Energy Discrepancy Analysis (Weekly)</h2>
            
            <h3 style="margin-top:15px; color:#3498db; font-size:14px;">ENERGY SUPPLY (Total: {total_supply:,.1f} kWh)</h3>
            <div class="energy-grid" style="grid-template-columns: repeat(auto-fit, minmax(120px, 150px)); justify-content: start;">
                {supply_cards}
            </div>
            
            <h3 style="margin-top:20px; color:#2ecc71; font-size:14px;">ENERGY ACCOUNTED (Total: {total_accounted:,.1f} kWh)</h3>
            <div class="energy-grid" style="grid-template-columns: repeat(auto-fit, minmax(120px, 150px)); justify-content: start;">
                {accounted_cards}
            </div>
            
            <h3 style="margin-top:20px; color:#e74c3c; font-size:14px;">THE DISCREPANCY (Supply − Accounted)</h3>
            <div class="energy-grid" style="grid-template-columns: 200px;">
                <div class="energy-card loss">
                    <h3 style="font-size:24px;">{discrepancy:,.1f}</h3>
                    <p>Unaccounted Loss<br>({loss_pct:.1f}%)</p>
                </div>
            </div>
        </div>
        """
    else:
        energy_comparison_section = """
        <div class="energy-comparison">
            <h2>⚡ Energy Discrepancy Analysis (Weekly)</h2>
            <p class="energy-unavailable">Energy data not available for this period (site_energy tables may not exist or have no data).</p>
        </div>
        """

    # 4. Save
    html = HTML_TEMPLATE.format(
        start_date=START_DATE_STR,
        end_date=end_dt.strftime("%Y-%m-%d"),
        target_weekly=target_weekly,
        total_meters=total_meters,
        avg_uptime=avg_uptime,
        perfect_meters=perfect_meters,
        dead_meters=dead_meters,
        energy_comparison_section=energy_comparison_section,
        rows=table_rows
    )

    os.makedirs(os.path.dirname(REPORT_FILE), exist_ok=True)
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
        
    print(f"Success! Weekly report generated: {REPORT_FILE}")
    webbrowser.open('file://' + os.path.realpath(REPORT_FILE))

if __name__ == "__main__":
    generate_report()