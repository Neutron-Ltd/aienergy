import mysql.connector
import pandas as pd
import warnings
import sys
import webbrowser
import os
import re

# Suppress warnings
warnings.filterwarnings('ignore')

# --- CONFIGURATION ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TARGET_DATE = "260201"       # YYMMDD (Feb 3rd, 2026)
REPORT_FILE = os.path.join(SCRIPT_DIR, "daily", f"Frequency-20{TARGET_DATE}.html")
EXPECTED_FREQ_MIN = 15       # Expected interval in minutes
EXPECTED_COUNT = int((24 * 60) / EXPECTED_FREQ_MIN) # Target: 96 records
EXCLUDED_METER_IDS = {1}     # 1 = 电网计量表 (main grid input table, not a meter)
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

# --- HTML CSS & TEMPLATE ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Meter Frequency Report: 20{date}</title>
    <style>
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f4f6f9; margin: 0; padding: 20px; color: #333; }}
        .container {{ max-width: 1400px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
        h1 {{ color: #2c3e50; border-bottom: 2px solid #eee; padding-bottom: 10px; }}
        
        .summary-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin-bottom: 30px; }}
        .card {{ background: #fff; border: 1px solid #ddd; padding: 20px; border-radius: 8px; text-align: center; }}
        .card h3 {{ margin: 0; font-size: 36px; color: #2c3e50; }}
        .card p {{ margin: 5px 0 0; color: #7f8c8d; font-weight: bold; text-transform: uppercase; font-size: 12px; }}
        .card.danger {{ border-left: 5px solid #e74c3c; }}
        .card.warning {{ border-left: 5px solid #f39c12; }}
        .card.success {{ border-left: 5px solid #27ae60; }}

        table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 13px; }}
        th {{ background-color: #f8f9fa; color: #2c3e50; padding: 12px; text-align: left; border-bottom: 2px solid #dee2e6; }}
        td {{ padding: 10px; border-bottom: 1px solid #eee; }}
        tr:hover {{ background-color: #f1f1f1; }}

        .badge {{ padding: 5px 10px; border-radius: 4px; font-weight: bold; font-size: 11px; display: inline-block; min-width: 60px; text-align: center; }}
        .bg-red {{ background-color: #ffeaea; color: #e74c3c; }}
        .bg-orange {{ background-color: #fff8e1; color: #f39c12; }}
        .bg-green {{ background-color: #e8f8f5; color: #27ae60; }}

        .progress-bg {{ background-color: #eee; border-radius: 10px; height: 8px; width: 80px; display: inline-block; margin-right: 10px; }}
        .progress-fill {{ height: 100%; border-radius: 10px; }}
        
        .time-col {{ font-family: monospace; color: #555; }}

        .energy-comparison {{ margin-top: 30px; padding: 20px; background: #fff; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.05); }}
        .energy-comparison h2 {{ margin-top: 0; color: #2c3e50; padding-bottom: 10px; border-bottom: 2px solid #eee; }}
        .energy-grid {{ display: grid; gap: 15px; margin-top: 20px; }}
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
        <h1>📊 Meter Frequency Report: 20{date}</h1>
        <p>Target: <strong>{target_count} records</strong> per day (Every {freq} mins)</p>
        
        <div class="summary-grid">
            <div class="card"><h3>{total}</h3><p>Total Meters</p></div>
            <div class="card danger"><h3>{dead}</h3><p>Dead (0 Data)</p></div>
            <div class="card warning"><h3>{low_perf}</h3><p>Low Performance</p></div>
            <div class="card success"><h3>{perfect}</h3><p>Perfect Count</p></div>
        </div>

        {energy_comparison_section}

        <h2>⚠️ Performance Analysis (Sorted by Uptime)</h2>
        {gap_table}
    </div>
</body>
</html>
"""

def _integrate_power_to_kwh(rows):
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


def fetch_daily_energy(target_date, meters_df):
    conn = mysql.connector.connect(**db_config)
    cursor = conn.cursor(dictionary=True)

    try:
        meter_info_df = pd.read_sql("SELECT meter_number, parent_meter_number FROM meter_info", conn)
    except Exception:
        conn.close()
        return None

    parent_numbers = set(int(x) for x in meter_info_df['parent_meter_number'].dropna().astype(int).tolist() if x != 0)
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

    # 1. Grid & Facility from site_energy
    energy_table = f"site_energy_{target_date}"
    try:
        query = f"""
            SELECT meter_number, MIN(ImpEP) AS min_imp, MAX(ImpEP) AS max_imp, MIN(ExpEP) AS min_exp, MAX(ExpEP) AS max_exp
            FROM {energy_table} GROUP BY meter_number
        """
        cursor.execute(query)
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
    except mysql.connector.Error:
        pass

    # 2. Solar & Storage via integration from site_power
    power_table = f"site_power_{target_date}"
    power_meter_ids = tuple(SOLAR_METER_IDS | {STORAGE_METER_ID})
    in_placeholders = ",".join(["%s"] * len(power_meter_ids))
    try:
        query = f"""
            SELECT meter_number, ts, Pt
            FROM {power_table}
            WHERE meter_number IN ({in_placeholders})
            ORDER BY meter_number, ts ASC
        """
        cursor.execute(query, power_meter_ids)
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


def analyze_meter_count(series):
    # 1. Count actual records
    actual_count = len(series)
    
    # 2. Get Start/End Times
    if actual_count > 0:
        start_str = series.min().strftime('%H:%M:%S')
        end_str = series.max().strftime('%H:%M:%S')
    else:
        start_str = "-"
        end_str = "-"

    # 3. Calculate Uptime % based on Target
    uptime = (actual_count / EXPECTED_COUNT) * 100
    if uptime > 100: uptime = 100 
    
    # 4. Calculate Average Insert Delay
    if actual_count > 1:
        start_time = series.min()
        end_time = series.max()
        duration_seconds = (end_time - start_time).total_seconds()
        
        avg_seconds = duration_seconds / (actual_count - 1)
        
        mins = int(avg_seconds // 60)
        secs = int(avg_seconds % 60)
        avg_delay_str = f"{mins}m {secs}s"
    else:
        avg_delay_str = "N/A"

    return actual_count, uptime, avg_delay_str, start_str, end_str

def generate_report():
    print(f"--- Generating Report for {TARGET_DATE} (Target: {EXPECTED_COUNT} records) ---")
    
    # Date Filtering Logic
    year = "20" + TARGET_DATE[0:2]
    month = TARGET_DATE[2:4]
    day = TARGET_DATE[4:6]
    full_date_str = f"{year}-{month}-{day}"
    print(f"Filtering Data Range: {full_date_str} 00:00:00 to 23:59:59")
    
    try:
        conn = mysql.connector.connect(**db_config)
        print("Fetching meter registry...")
        meters_df = pd.read_sql("SELECT meter_number, meter_name, mac_address FROM meter_info", conn)
        
        table_name = f"site_power_{TARGET_DATE}"
        print(f"Reading logs from {table_name}...")
        
        # Strict Query
        query = f"""
            SELECT meter_number, ts 
            FROM {table_name}
            WHERE ts >= '{full_date_str} 00:00:00'
            AND ts <= '{full_date_str} 23:59:59'
        """
        all_data = pd.read_sql(query, conn)

        # --- Energy Comparison: Supply vs Accounted vs Discrepancy ---
        energy_data = fetch_daily_energy(TARGET_DATE, meters_df)
        conn.close()
        
    except Exception as e:
        print(f"Error: {e}")
        return

    # Exclude non-meter entries (e.g. main grid input table)
    meters_df = meters_df[~meters_df['meter_number'].isin(EXCLUDED_METER_IDS)]

    # Data Prep
    all_data['ts'] = pd.to_datetime(all_data['ts'])
    grouped = all_data.groupby('meter_number')
    meters_with_data = set(grouped.groups.keys())

    stats_list = []
    dead_count = 0
    perfect_count = 0
    low_perf_count = 0

    print("Analyzing meters...")

    for index, row in meters_df.iterrows():
        m_id = row['meter_number']
        m_name = row['meter_name']
        
        # DEAD METERS
        if m_id not in meters_with_data:
            stats_list.append({
                'id': m_id,
                'name': m_name,
                'count': 0,
                'uptime': 0.0,
                'avg_delay': 'OFFLINE',
                'start': '-',
                'end': '-'
            })
            dead_count += 1
            continue

        # ACTIVE METERS
        ts_series = grouped.get_group(m_id)['ts']
        actual_count, uptime, avg_delay, start_time, end_time = analyze_meter_count(ts_series)
        
        stats_list.append({
            'id': m_id,
            'name': m_name,
            'count': actual_count,
            'uptime': round(uptime, 1),
            'avg_delay': avg_delay,
            'start': start_time,
            'end': end_time
        })
        
        if uptime >= 98.0:
            perfect_count += 1
        elif uptime > 0:
            low_perf_count += 1

    # Sort by Uptime (Lowest first)
    stats_list.sort(key=lambda x: x['uptime'])

    # --- GENERATE HTML TABLE ---
    table_rows = ""
    for m in stats_list:
        # Colors
        if m['uptime'] == 0: 
            color = "#e74c3c" # Red
            badge = "<span class='badge bg-red'>OFFLINE</span>"
        elif m['uptime'] < 90: 
            color = "#f39c12" # Orange
            badge = "<span class='badge bg-orange'>LOW</span>"
        else: 
            color = "#27ae60" # Green
            badge = "<span class='badge bg-green'>OK</span>"

        # Highlight count
        count_display = f"<b>{m['count']}</b>"
        if m['count'] < EXPECTED_COUNT:
            count_display = f"<span style='color:#e74c3c'><b>{m['count']}</b></span>"

        table_rows += f"""
        <tr>
            <td><b>{m['id']}</b></td>
            <td>{m['name']}</td>
            <td>
                <div style="display:flex; align-items:center;">
                    <div class="progress-bg"><div class="progress-fill" style="width:{m['uptime']}%; background-color:{color};"></div></div>
                    <strong>{m['uptime']}%</strong>
                </div>
            </td>
            <td>{count_display} / {EXPECTED_COUNT}</td>
            <td>{m['avg_delay']}</td>
            <td class="time-col">{m['start']}</td>
            <td class="time-col">{m['end']}</td>
            <td>{badge}</td>
        </tr>
        """
    
    html_table = f"""
    <table>
        <thead>
            <tr>
                <th>ID</th>
                <th>Name</th>
                <th>Uptime (Based on Count)</th>
                <th>Records</th>
                <th>Avg Interval</th>
                <th>First Seen</th>
                <th>Last Seen</th>
                <th>Status</th>
            </tr>
        </thead>
        <tbody>{table_rows}</tbody>
    </table>
    """

    # --- ENERGY COMPARISON SECTION ---
    if energy_data is not None:
        d = energy_data
        total_supply = d['grid_import'] + d['solar_kwh'] + d['storage_discharge']
        total_accounted = d['facility_consumption'] + d['storage_charge'] + d['grid_export']
        discrepancy = total_supply - total_accounted
        loss_pct = (discrepancy / total_supply * 100) if total_supply > 0 else 0

        supply_cards = f"""
            <div class="energy-card supply"><h3>{d['grid_import']:,.1f}</h3><p>Grid Import<br>(Supply)</p></div>
            <div class="math-sign">+</div>
            <div class="energy-card supply"><h3>{d['solar_kwh']:,.1f}</h3><p>Solar Gen<br>(Supply)</p></div>
        """
        if d['storage_discharge'] > 0:
            supply_cards += f"""<div class="math-sign">+</div><div class="energy-card supply"><h3>{d['storage_discharge']:,.1f}</h3><p>Battery Discharge<br>(Supply)</p></div>"""

        accounted_cards = f"""
            <div class="energy-card accounted"><h3>{d['facility_consumption']:,.1f}</h3><p>Facility Load<br>(Accounted)</p></div>
        """
        if d['storage_charge'] > 0:
            accounted_cards += f"""<div class="math-sign">+</div><div class="energy-card accounted"><h3>{d['storage_charge']:,.1f}</h3><p>Battery Charge<br>(Accounted)</p></div>"""
        if d['grid_export'] > 0:
            accounted_cards += f"""<div class="math-sign">+</div><div class="energy-card accounted"><h3>{d['grid_export']:,.1f}</h3><p>Grid Export<br>(Accounted)</p></div>"""

        energy_comparison_section = f"""
        <div class="energy-comparison">
            <h2>⚡ Energy Discrepancy Analysis (Daily)</h2>
            <h3 style="margin-top:15px; color:#3498db; font-size:14px;">ENERGY SUPPLY (Total: {total_supply:,.1f} kWh)</h3>
            <div class="energy-grid" style="grid-template-columns: repeat(auto-fit, minmax(120px, 150px)); justify-content: start;">{supply_cards}</div>
            <h3 style="margin-top:20px; color:#2ecc71; font-size:14px;">ENERGY ACCOUNTED (Total: {total_accounted:,.1f} kWh)</h3>
            <div class="energy-grid" style="grid-template-columns: repeat(auto-fit, minmax(120px, 150px)); justify-content: start;">{accounted_cards}</div>
            <h3 style="margin-top:20px; color:#e74c3c; font-size:14px;">THE DISCREPANCY (Supply − Accounted)</h3>
            <div class="energy-grid" style="grid-template-columns: 200px;">
                <div class="energy-card loss">
                    <h3 style="font-size:24px;">{discrepancy:,.1f}</h3><p>Unaccounted Loss<br>({loss_pct:.1f}%)</p>
                </div>
            </div>
        </div>
        """
    else:
        energy_comparison_section = """
        <div class="energy-comparison">
            <h2>⚡ Energy Discrepancy Analysis (Daily)</h2>
            <p class="energy-unavailable">Energy data not available for this date.</p>
        </div>
        """

    # --- FILL TEMPLATE ---
    final_html = HTML_TEMPLATE.format(
        date=TARGET_DATE,
        freq=EXPECTED_FREQ_MIN,
        target_count=EXPECTED_COUNT,
        total=len(meters_df),
        dead=dead_count,
        low_perf=low_perf_count,
        perfect=perfect_count,
        energy_comparison_section=energy_comparison_section,
        gap_table=html_table
    )

    os.makedirs(os.path.dirname(REPORT_FILE), exist_ok=True)
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(final_html)

    print(f"Success! Report generated: {REPORT_FILE}")
    webbrowser.open('file://' + os.path.realpath(REPORT_FILE))

if __name__ == "__main__":
    generate_report()