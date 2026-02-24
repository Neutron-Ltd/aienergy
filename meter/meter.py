import os
import argparse
import mysql.connector
import pandas as pd
import warnings
import matplotlib.pyplot as plt
import io
import base64
from datetime import datetime

# Suppress warnings
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

def fetch_meter_info(cursor, meter_id):
    cursor.execute("SELECT meter_name, type_code, ratio FROM meter_info WHERE meter_number = %s", (meter_id,))
    res = cursor.fetchone()
    if res:
        return {"name": res[0], "type": res[1], "ratio": res[2]}
    return {"name": "Unknown", "type": "Unknown", "ratio": 1}

def detect_meter_type(df_power, df_energy):
    """Detects the functional category of the meter based on live data behavior."""
    if df_power.empty and not df_energy.empty:
        return "Energy-Only", "bg-purple-100 text-purple-800 border-purple-300", "Usually Main Grid Utility Meters"
    
    energy_is_zero = True
    if not df_energy.empty:
        for col in ['TotalEP', 'ImpEP', 'ExpEP']:
            if col in df_energy.columns and df_energy[col].max() > 0:
                energy_is_zero = False
                break
                
    if not df_power.empty and energy_is_zero:
        return "Power-Only", "bg-amber-100 text-amber-800 border-amber-300", "Solar or Storage with inactive energy registers"
        
    if not df_power.empty and not df_energy.empty:
        return "Standard Load", "bg-blue-100 text-blue-800 border-blue-300", "Regular facility infrastructure & machinery"
        
    return "No Data", "bg-slate-100 text-slate-800 border-slate-300", "Device offline or unresponsive"

def generate_gap_plot(df, title="Communication Rhythm"):
    """Generates the base64 plot for the interval gaps."""
    if df.empty or 'delta' not in df.columns:
        return ""
    
    plot_df = df.dropna(subset=['delta'])
    secs = plot_df['delta'].dt.total_seconds() / 60.0
    secs = secs.clip(upper=60)

    plt.figure(figsize=(10, 3))
    plt.bar(plot_df['ts'], secs, color='#3b82f6', width=0.01)
    plt.axhline(y=15, color='r', linestyle='--', label='15m Target')
    plt.title(f'{title} (Minutes between packets)', fontsize=10)
    plt.ylabel('Minutes')
    plt.grid(axis='y', linestyle='--', alpha=0.3)
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=100)
    plt.close()
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')

def main(meter_id, date_suffix):
    report_file = os.path.join(DATA_DIR, f"meter-{meter_id}-{date_suffix}.html")
    print(f"Connecting to analyze Meter {meter_id} on {date_suffix}...")

    try:
        conn = mysql.connector.connect(host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASS, database=DB_NAME)
        cursor = conn.cursor()
        info = fetch_meter_info(cursor, meter_id)

        # Fetch Data
        try:
            cursor.execute(f"SELECT * FROM site_power_{date_suffix} WHERE meter_number = %s ORDER BY ts ASC", (meter_id,))
            df_power = pd.DataFrame(cursor.fetchall(), columns=[col[0] for col in cursor.description])
        except: df_power = pd.DataFrame()

        try:
            cursor.execute(f"SELECT * FROM site_energy_{date_suffix} WHERE meter_number = %s ORDER BY ts ASC", (meter_id,))
            df_energy = pd.DataFrame(cursor.fetchall(), columns=[col[0] for col in cursor.description])
        except: df_energy = pd.DataFrame()

        cursor.close()
        conn.close()
    except Exception as e:
        print(f"❌ Database Error: {e}")
        return

    if df_power.empty and df_energy.empty:
        print(f"❌ No data found for Meter {meter_id} on {date_suffix}.")
        return

    # Process Time Deltas
    if not df_power.empty:
        df_power['ts'] = pd.to_datetime(df_power['ts'])
        df_power['delta'] = df_power['ts'].diff()
    if not df_energy.empty:
        df_energy['ts'] = pd.to_datetime(df_energy['ts'])
        df_energy['delta'] = df_energy['ts'].diff()

    # Detect Profile
    m_type, m_color, m_desc = detect_meter_type(df_power, df_energy)
    print(f"Detected Profile: {m_type}")

    # --- DYNAMIC CONTENT GENERATION ---
    plot_b64 = ""
    rows_html = ""
    energy_html = ""
    table_headers = ""

    # 1. Energy-Only Logic (Main Grid)
    if m_type == "Energy-Only":
        plot_b64 = generate_gap_plot(df_energy, "Energy Reporting Rhythm")
        total_records = len(df_energy)
        
        # Calculate Energy
        first_e = df_energy.iloc[0]
        last_e = df_energy.iloc[-1]
        imp = (last_e.get('ImpEP', 0) - first_e.get('ImpEP', 0)) * info['ratio']
        exp = (last_e.get('ExpEP', 0) - first_e.get('ExpEP', 0)) * info['ratio']
        
        energy_html = f"""
            <tr><td class='font-semibold'>Imported (Grid Draw)</td><td class='text-right text-red-600 font-bold'>{imp:.2f} kWh</td></tr>
            <tr><td class='font-semibold'>Exported (Grid Feed)</td><td class='text-right text-green-600 font-bold'>{exp:.2f} kWh</td></tr>
        """
        
        table_headers = "<th>Time</th><th>Gap</th><th>Total Active (TotalEP)</th><th>Import (ImpEP)</th><th>Export (ExpEP)</th>"
        for idx, row in df_energy.head(100).iterrows():
            ts_str = row['ts'].strftime('%H:%M:%S')
            mins = int(row['delta'].total_seconds() // 60) if pd.notnull(row['delta']) else 0
            gap_style = "text-slate-500" if mins==0 else "text-green-600" if 14<=mins<=16 else "text-red-500 font-bold"
            rows_html += f"<tr><td>{ts_str}</td><td class='{gap_style}'>{mins}m</td><td>{row.get('TotalEP', 0)}</td><td>{row.get('ImpEP', 0)}</td><td>{row.get('ExpEP', 0)}</td></tr>"

    # 2. Power-Only Logic (Solar/Storage Anomalies)
    elif m_type == "Power-Only":
        plot_b64 = generate_gap_plot(df_power, "Power Reporting Rhythm")
        total_records = len(df_power)
        
        energy_html = "<tr><td colspan='2' class='text-center text-red-600 font-bold py-4'>⚠️ Hardware Anomaly: Cumulative Energy Registers are currently inactive (reporting zeros). Unable to calculate daily kWh yield.</td></tr>"
        
        table_headers = "<th>Time</th><th>Gap</th><th>Voltage (Uan)</th><th>Current (Ia)</th><th>Active Power (Pt)</th>"
        for idx, row in df_power.head(100).iterrows():
            ts_str = row['ts'].strftime('%H:%M:%S')
            mins = int(row['delta'].total_seconds() // 60) if pd.notnull(row['delta']) else 0
            gap_style = "text-slate-500" if mins==0 else "text-green-600" if 14<=mins<=16 else "text-red-500 font-bold"
            
            # Highlight negative Pt (Generation)
            pt = row.get('Pt', 0)
            pt_style = "text-green-600 font-bold bg-green-50 rounded px-1" if pt < 0 else "text-slate-800"
            
            rows_html += f"<tr><td>{ts_str}</td><td class='{gap_style}'>{mins}m</td><td>{row.get('Uan', 0)}</td><td>{row.get('Ia', 0)}</td><td><span class='{pt_style}'>{pt}</span></td></tr>"

    # 3. Standard Load Logic (Normal Machines)
    else:
        plot_b64 = generate_gap_plot(df_power, "Telemetry Rhythm")
        total_records = len(df_power)
        
        first_e = df_energy.iloc[0]
        last_e = df_energy.iloc[-1]
        imp = (last_e.get('ImpEP', 0) - first_e.get('ImpEP', 0)) * info['ratio']
        
        energy_html = f"<tr><td class='font-semibold'>Daily Consumption</td><td class='text-right text-blue-600 font-bold'>{imp:.2f} kWh</td></tr>"
        
        table_headers = "<th>Time</th><th>Gap</th><th>Voltage (Uan)</th><th>Current (Ia)</th><th>Active Power (Pt)</th>"
        for idx, row in df_power.head(100).iterrows():
            ts_str = row['ts'].strftime('%H:%M:%S')
            mins = int(row['delta'].total_seconds() // 60) if pd.notnull(row['delta']) else 0
            gap_style = "text-slate-500" if mins==0 else "text-green-600" if 14<=mins<=16 else "text-red-500 font-bold"
            rows_html += f"<tr><td>{ts_str}</td><td class='{gap_style}'>{mins}m</td><td>{row.get('Uan', 0)}</td><td>{row.get('Ia', 0)}</td><td>{row.get('Pt', 0)}</td></tr>"

    # --- RENDER HTML ---
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Meter {meter_id} - Dynamic Analysis</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style> table {{ border-collapse: collapse; width: 100%; }} th, td {{ padding: 10px 14px; text-align: left; border-bottom: 1px solid #e2e8f0; }} th {{ background-color: #f8fafc; font-weight: 600; color: #475569; }} tr:hover {{ background-color: #f1f5f9; }} </style>
    </head>
    <body class="bg-slate-50 text-slate-800 font-sans p-4 md:p-8">
        <div class="max-w-6xl mx-auto space-y-6">
            
            <header class="bg-slate-900 text-white p-6 rounded-xl shadow-md flex justify-between items-center border-t-4 border-blue-500">
                <div>
                    <div class="flex items-center gap-3 mb-2">
                        <span class="px-3 py-1 text-xs font-bold uppercase tracking-wider rounded-md border {m_color}">{m_type}</span>
                        <span class="text-xs text-slate-400">{m_desc}</span>
                    </div>
                    <h1 class="text-3xl font-extrabold flex items-center gap-3">
                        Meter {meter_id} <span class="text-xl font-normal text-slate-300">| {info['name']}</span>
                    </h1>
                    <p class="text-slate-400 mt-2">Data Date: <span class="text-white font-mono">{date_suffix}</span> | CT Ratio Multiplier: {info['ratio']}</p>
                </div>
                <div class="text-right">
                    <p class="text-3xl font-bold text-blue-400">{total_records}</p>
                    <p class="text-xs text-slate-400 uppercase tracking-wider mt-1">Total Logs Found</p>
                </div>
            </header>

            <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
                <div class="col-span-1 bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
                    <div class="bg-slate-800 p-3 text-white font-bold flex justify-between items-center">
                        Daily Energy 
                        <span class="text-xs font-normal text-slate-300">Multiplied by CT Ratio</span>
                    </div>
                    <table class="w-full text-sm">
                        <tbody>{energy_html}</tbody>
                    </table>
                </div>
                
                <div class="col-span-1 md:col-span-2 bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
                    <div class="bg-slate-800 p-3 text-white font-bold">Data Frequency & Gaps</div>
                    <div class="p-4 flex justify-center bg-slate-50">
                        <img src="data:image/png;base64,{plot_b64}" alt="Interval Plot" class="w-full max-h-48 object-contain">
                    </div>
                </div>
            </div>

            <div class="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
                <div class="bg-slate-800 p-3 text-white font-bold flex justify-between">
                    <span>Raw Data Sample</span>
                    <span class="text-xs font-normal text-slate-300 text-right">Showing first 100 entries</span>
                </div>
                <div class="overflow-y-auto max-h-96">
                    <table class="w-full text-sm whitespace-nowrap">
                        <thead class="sticky top-0 shadow-sm z-10">{table_headers}</thead>
                        <tbody>{rows_html}</tbody>
                    </table>
                </div>
            </div>

        </div>
    </body>
    </html>
    """

    with open(report_file, "w", encoding="utf-8") as f:
        f.write(html_content)
    
    print(f"✅ Success! Report generated at: {report_file}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dynamically analyze a specific meter.")
    parser.add_argument("--id", type=int, required=True, help="Target Meter ID (e.g., 29)")
    parser.add_argument("--date", type=str, required=True, help="Target Date Suffix (YYMMDD, e.g., 260202)")
    
    args = parser.parse_args()
    main(args.id, args.date)