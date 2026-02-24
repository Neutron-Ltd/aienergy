import os
import argparse
import mysql.connector
import pandas as pd
import warnings
from datetime import datetime

warnings.filterwarnings('ignore')

# --- CONFIGURATION ---
DB_HOST = "localhost"
DB_PORT = 3307
DB_USER = "energy_user"
DB_PASS = "REDACTED"
DB_NAME = "unified"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# The Curated List of Representative Meters
CURATED_METERS = [
    {"id": 1, "category": "1. Main Grid & Transformers", "name": "Utility Grid Main Meter", "expected": "Expect high ImpEP & Pt. May lack instantaneous power logs."},
    {"id": 29, "category": "2. Renewable Energy & Storage", "name": "Phase 1 PV Generation", "expected": "Expect ExpEP & negative Pt when generating."},
    {"id": 30, "category": "2. Renewable Energy & Storage", "name": "Pylontech Energy Storage", "expected": "Expect both ImpEP (Charging) and ExpEP (Discharging)."},
    {"id": 13, "category": "3. Infrastructure", "name": "Ground Outdoor Streetlight", "expected": "Expect night-time load, zero during the day."},
    {"id": 55, "category": "4. Building 1", "name": "Bldg 1 - 1F Lighting & AC", "expected": "Expect standard 9-to-5 office consumption curve."},
    {"id": 189, "category": "6. Building 3A", "name": "Automated Warehouse AS/RS", "expected": "Expect highly variable, robotic motor loads."},
    {"id": 198, "category": "7. Building 3B", "name": "Main Bending Machine", "expected": "Expect bursty, sudden spikes in power."}
]

POWER_COLS = {
    "Uan": "Voltage A", "Ubn": "Voltage B", "Ucn": "Voltage C",
    "Ia": "Current A", "Ib": "Current B", "Ic": "Current C",
    "Pt": "Total Active Power", "Qt": "Total Reactive Power", "FPt": "Power Factor"
}

ENERGY_COLS = {
    "TotalEP": "Total Active Energy", 
    "ImpEP": "Import Energy", 
    "ExpEP": "Export Energy", 
    "TotalEQ": "Total Reactive Energy"
}

def check_availability(df, columns_dict):
    """Analyzes a dataframe and returns the status of each requested column."""
    results = []
    for col, label in columns_dict.items():
        if df.empty or col not in df.columns:
            status = "Not Logged"
            color = "bg-slate-100 text-slate-500 border-slate-200"
            icon = "∅"
        elif df[col].isnull().all() or (df[col] == 0).all():
            status = "Inactive (Zeros)"
            color = "bg-red-50 text-red-600 border-red-200"
            icon = "0"
        else:
            status = "Active"
            color = "bg-green-50 text-green-700 border-green-200 font-medium"
            icon = "✓"
            
        results.append(f"""
            <div class='flex items-center justify-between p-2 border rounded-md {color}'>
                <span class='text-xs uppercase tracking-wider'>{col} <span class='text-opacity-70 ml-1'>({label})</span></span>
                <span class='text-sm flex items-center gap-1'>{icon} {status}</span>
            </div>
        """)
    return "".join(results)

def main(date_suffix):
    report_file = os.path.join(DATA_DIR, f"category-{date_suffix}.html")
    print(f"Connecting to database to analyze curated categories for {date_suffix}...")

    try:
        conn = mysql.connector.connect(host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASS, database=DB_NAME)
        cursor = conn.cursor()
    except Exception as e:
        print(f"❌ Database Connection Error: {e}")
        return

    cards_html = ""

    for meter in CURATED_METERS:
        mid = meter['id']
        print(f"Analyzing Meter {mid} ({meter['category']})...")
        
        # Fetch Power Data
        try:
            cursor.execute(f"SELECT * FROM site_power_{date_suffix} WHERE meter_number = %s", (mid,))
            p_cols = [col[0] for col in cursor.description]
            df_power = pd.DataFrame(cursor.fetchall(), columns=p_cols)
        except:
            df_power = pd.DataFrame()

        # Fetch Energy Data
        try:
            cursor.execute(f"SELECT * FROM site_energy_{date_suffix} WHERE meter_number = %s", (mid,))
            e_cols = [col[0] for col in cursor.description]
            df_energy = pd.DataFrame(cursor.fetchall(), columns=e_cols)
        except:
            df_energy = pd.DataFrame()

        # Generate Availability Blocks
        power_html = check_availability(df_power, POWER_COLS)
        energy_html = check_availability(df_energy, ENERGY_COLS)

        cards_html += f"""
        <div class="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden mb-8">
            <div class="bg-slate-800 p-4 text-white">
                <div class="flex items-center justify-between">
                    <div>
                        <span class="text-xs font-bold uppercase tracking-wider text-blue-300">{meter['category']}</span>
                        <h2 class="text-2xl font-extrabold mt-1">Meter ID: {mid} <span class="text-lg font-normal text-slate-300 ml-2">| {meter['name']}</span></h2>
                    </div>
                    <div class="text-right">
                        <div class="text-sm"><span class="font-bold text-blue-400">{len(df_power)}</span> Power Logs</div>
                        <div class="text-sm"><span class="font-bold text-green-400">{len(df_energy)}</span> Energy Logs</div>
                    </div>
                </div>
                <p class="text-sm text-slate-400 mt-2 border-t border-slate-700 pt-2">💡 <b>Expected Behavior:</b> {meter['expected']}</p>
            </div>
            
            <div class="grid grid-cols-1 md:grid-cols-2 gap-0">
                <div class="p-6 border-r border-slate-100">
                    <h3 class="font-bold text-slate-700 mb-4 border-b pb-2">⚡ Instantaneous Power (Speedometer)</h3>
                    <div class="space-y-2">
                        {power_html}
                    </div>
                </div>
                <div class="p-6 bg-slate-50">
                    <h3 class="font-bold text-slate-700 mb-4 border-b pb-2">🔋 Cumulative Energy (Odometer)</h3>
                    <div class="space-y-2">
                        {energy_html}
                    </div>
                </div>
            </div>
        </div>
        """

    cursor.close()
    conn.close()

    # --- GENERATE HTML ---
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Category Data Availability</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-100 text-slate-800 font-sans p-4 md:p-8">
        <div class="max-w-6xl mx-auto">
            <header class="text-center space-y-4 mb-10">
                <h1 class="text-4xl font-extrabold text-slate-900">Categorical Data Availability Report</h1>
                <p class="text-slate-500 text-lg">Scanning representative meters across all facility categories to verify hardware logging status.</p>
                <div class="inline-block bg-white px-4 py-2 rounded-full border shadow-sm text-sm font-mono text-slate-600">
                    Target Date: <span class="font-bold text-blue-600">{date_suffix}</span> | Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
                </div>
            </header>

            {cards_html}

        </div>
    </body>
    </html>
    """

    with open(report_file, "w", encoding="utf-8") as f:
        f.write(html_content)
    
    print(f"\n✅ Scan Complete! Report generated successfully at: {report_file}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scan data availability across representative meter categories.")
    parser.add_argument("--date", type=str, default="260202", help="Target Date Suffix (YYMMDD, e.g., 260202)")
    
    args = parser.parse_args()
    main(args.date)