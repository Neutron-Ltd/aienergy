import os
import argparse
import webbrowser
from pathlib import Path
import mysql.connector
import json
import pandas as pd
from datetime import datetime, timedelta

# --- CONFIGURATION ---
DB_CONFIG = {
    "host": "localhost",
    "port": 3307,
    "user": "energy_user",
    "password": "REDACTED",
    "database": "unified"
}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_FILE = os.path.join(SCRIPT_DIR, "..", "relation.json")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "data")
os.makedirs(OUTPUT_DIR, exist_ok=True)

def get_end_meters(node, end_meter_list=None):
    """Recursively traverses relation.json and extracts ONLY nodes with no sub_nodes (Leaf Nodes)."""
    if end_meter_list is None:
        end_meter_list = []
    
    sub_nodes = node.get("sub_nodes", [])
    if not sub_nodes:
        if "id" in node:
            end_meter_list.append({
                "id": node["id"],
                "name": node.get("name", f"Node {node['id']}"),
                "category": node.get("category", "Unknown")
            })
    else:
        for sub in sub_nodes:
            get_end_meters(sub, end_meter_list)
        
    return end_meter_list

def generate_date_range(start_date_str, days=7):
    """Generates a list of YYMMDD strings for the target period."""
    start_dt = datetime.strptime(f"20{start_date_str}", "%Y%m%d")
    return [(start_dt + timedelta(days=i)).strftime("%y%m%d") for i in range(days)]

def build_status_badge(data):
    """Generates the HTML badge based on data completeness."""
    if not data or data['actual'] == 0:
        return """
        <div class="px-2 py-1 rounded bg-slate-50 border border-slate-200 text-slate-400 text-center">
            <div class="text-[10px] font-bold">NO DATA</div>
            <div class="text-[9px] font-mono opacity-70 mt-0.5">Offline</div>
        </div>
        """
        
    actual = data['actual']
    expected = data['expected']
    interval = data['interval']
    
    # Calculate health percentage
    health = (actual / expected) * 100 if expected > 0 else 0
    
    if health >= 95:
        color_class = "bg-emerald-50 text-emerald-700 border-emerald-200"
    elif health >= 50:
        color_class = "bg-amber-50 text-amber-700 border-amber-300"
    else:
        color_class = "bg-rose-50 text-rose-700 border-rose-200"
        
    return f"""
    <div class="px-2 py-1 rounded border {color_class} text-center shadow-sm">
        <div class="text-[11px] font-bold tracking-tight">F {actual}/{expected}</div>
        <div class="text-[9px] font-mono mt-0.5 opacity-80">I {interval:.1f}m</div>
    </div>
    """

def main(start_date, days):
    dates = generate_date_range(start_date, days)
    end_date_str = dates[-1]
    report_file = os.path.join(OUTPUT_DIR, f"endmetersping-{start_date}.html")
    
    print(f"🗓️ Generating 7-Day Continuity Matrix ({start_date} to {end_date_str})...")

    # 1. Load Topology & Filter for End Meters
    try:
        with open(JSON_FILE, 'r', encoding='utf-8') as f:
            tree_data = json.load(f)
        end_nodes = get_end_meters(tree_data.get("root_node", {}))
        end_meter_ids = [n['id'] for n in end_nodes]
    except Exception as e:
        print(f"❌ JSON Error: {e}")
        return

    if not end_meter_ids:
        print("❌ No end meters found in relation.json.")
        return

    # Initialize data matrix: matrix[node_id][date_str] = data_dict
    data_matrix = {n['id']: {d: None for d in dates} for n in end_nodes}

    # 2. Database Bulk Processing
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
        
        for date_str in dates:
            table_name = f"site_power_{date_str}"
            print(f"  > Scanning {table_name}...")
            
            # Format the query for strict 24h boundaries on targeted nodes
            format_strings = ','.join(['%s'] * len(end_meter_ids))
            query = f"""
                SELECT meter_number, ts 
                FROM {table_name} 
                WHERE meter_number IN ({format_strings})
                AND ts >= '20{date_str[0:2]}-{date_str[2:4]}-{date_str[4:6]} 00:00:00'
                AND ts <= '20{date_str[0:2]}-{date_str[2:4]}-{date_str[4:6]} 23:59:59'
                ORDER BY meter_number, ts ASC
            """
            
            try:
                cursor.execute(query, tuple(end_meter_ids))
                rows = cursor.fetchall()
                
                if rows:
                    df = pd.DataFrame(rows)
                    df['ts'] = pd.to_datetime(df['ts'])
                    
                    # Group by meter to calculate median intervals and counts
                    grouped = df.groupby('meter_number')
                    for meter_id, group in grouped:
                        actual_count = len(group)
                        
                        if actual_count > 1:
                            median_interval = group['ts'].diff().dt.total_seconds().median() / 60.0
                            # Failsafe for rapid duplicate logs
                            if pd.isna(median_interval) or median_interval < 1:
                                median_interval = 15.0
                        else:
                            median_interval = 15.0 # Default assumption if only 1 log exists
                            
                        expected_count = int(1440 // median_interval) if median_interval > 0 else 96
                        
                        # Store the result
                        data_matrix[meter_id][date_str] = {
                            'actual': actual_count,
                            'expected': expected_count,
                            'interval': median_interval
                        }
            except mysql.connector.Error as err:
                print(f"    [!] Table {table_name} may not exist or error occurred: {err}")
                
        conn.close()
    except Exception as e:
        print(f"❌ Database Error: {e}")
        return

    # --- STATISTICS ---
    total_logs_act = 0
    total_logs_exp = 0
    meter_days_with_data = 0
    for nid in data_matrix:
        for d in dates:
            cell = data_matrix[nid][d]
            if cell and cell.get('actual', 0) > 0:
                total_logs_act += cell['actual']
                total_logs_exp += cell['expected']
                meter_days_with_data += 1
    overall_rel = (total_logs_act / total_logs_exp * 100) if total_logs_exp > 0 else 0
    total_cells = len(end_nodes) * len(dates)
    coverage_pct = (meter_days_with_data / total_cells * 100) if total_cells > 0 else 0

    # --- UI GENERATION ---
    date_headers = "".join([f"<th class='px-3 py-4 border-b border-slate-200 text-center text-xs font-bold text-slate-600 bg-slate-50'>{d}</th>" for d in dates])
    
    rows_html = ""
    for node in end_nodes:
        nid = node['id']
        cells = ""
        for d in dates:
            cell_data = data_matrix[nid][d]
            cells += f"<td class='px-2 py-3 border-b border-slate-100 align-middle'>{build_status_badge(cell_data)}</td>"
            
        rows_html += f"""
        <tr class="hover:bg-slate-50 transition-colors bg-white">
            <td class="px-4 py-3 border-b border-slate-100 font-mono text-xs text-slate-500 w-16 align-middle">ID:{nid}</td>
            <td class="px-4 py-3 border-b border-slate-100 text-xs text-slate-800 font-medium align-middle whitespace-normal min-w-[200px] leading-tight">{node['name']}</td>
            <td class="px-4 py-3 border-b border-slate-100 text-[10px] uppercase text-slate-400 tracking-widest align-middle">{node['category']}</td>
            {cells}
        </tr>
        """

    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8"><title>AIENERGY - 7-Day End Meter Continuity</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            ::-webkit-scrollbar {{ width: 8px; height: 8px; }}
            ::-webkit-scrollbar-track {{ background: #f8fafc; }}
            ::-webkit-scrollbar-thumb {{ background: #cbd5e1; border-radius: 4px; }}
            ::-webkit-scrollbar-thumb:hover {{ background: #94a3b8; }}
            @media print {{
                @page {{ size: landscape; margin: 10mm; }}
                body {{ background: white; -webkit-print-color-adjust: exact; }}
                .shadow-sm {{ box-shadow: none; border-color: #e2e8f0; }}
            }}
        </style>
    </head>
    <body class="bg-slate-100 min-h-screen font-sans text-slate-900 pb-12">
        <div class="max-w-[1600px] mx-auto mt-8 px-6 space-y-8">
            <header class="bg-white p-8 rounded-2xl shadow-sm border border-slate-200 flex justify-between items-center">
                <div>
                    <h1 class="text-3xl font-black text-slate-900">7-Day End Meter Continuity Matrix</h1>
                    <p class="text-slate-500 mt-2 text-sm">Tracking data frequency and detection intervals for <strong class="text-slate-700">Leaf Nodes Only</strong> | <span class="font-mono">20{start_date} - 20{end_date_str}</span></p>
                </div>
                <div class="flex items-center gap-6">
                    <div class="bg-slate-100 p-3 rounded-lg border border-slate-200 text-right">
                        <div class="text-[10px] font-bold text-slate-400 uppercase tracking-widest">End Meters Tracked</div>
                        <div class="text-xl font-black text-slate-700">{len(end_nodes)} Nodes</div>
                    </div>
                    <div class="flex gap-3 text-[10px] uppercase font-bold tracking-widest">
                        <span class="px-3 py-2 bg-emerald-50 text-emerald-700 border border-emerald-200 rounded flex items-center gap-2 shadow-sm"><span class="w-2 h-2 rounded-full bg-emerald-500"></span> > 95%</span>
                        <span class="px-3 py-2 bg-amber-50 text-amber-700 border border-amber-300 rounded flex items-center gap-2 shadow-sm"><span class="w-2 h-2 rounded-full bg-amber-500"></span> 50-94%</span>
                        <span class="px-3 py-2 bg-rose-50 text-rose-700 border border-rose-200 rounded flex items-center gap-2 shadow-sm"><span class="w-2 h-2 rounded-full bg-rose-500"></span> < 50%</span>
                    </div>
                </div>
            </header>

            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div class="bg-white p-6 rounded-2xl shadow-sm border border-slate-200">
                    <span class="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Aggregate Reliability</span>
                    <div class="text-3xl font-black text-slate-800 mt-1">{overall_rel:.1f}%</div>
                    <div class="w-full bg-slate-100 h-1.5 rounded-full mt-3 overflow-hidden"><div class="bg-slate-700 h-full" style="width: {overall_rel}%"></div></div>
                </div>
                <div class="bg-slate-900 p-6 rounded-2xl shadow-xl text-white">
                    <span class="text-[10px] font-bold text-slate-400 uppercase tracking-widest text-opacity-80">Meter-Days With Data</span>
                    <div class="text-3xl font-black mt-1 text-white">{meter_days_with_data}<span class="text-slate-400 font-normal"> / {total_cells}</span> <span class="text-sm font-light text-slate-400">({coverage_pct:.0f}%)</span></div>
                </div>
            </div>

            <section class="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden mb-8">
                <div class="bg-slate-900 p-4 flex justify-between items-center text-white">
                    <div>
                        <h2 class="text-lg font-bold tracking-widest">Hardware Telemetry Timeline</h2>
                        <p class="text-[10px] text-slate-400 uppercase mt-1">Target Period: 20{start_date} to 20{end_date_str}</p>
                    </div>
                    <div class="text-right text-[10px] text-slate-300 font-mono">
                        F = Actual / Expected Logs <br> I = Median Interval (mins)
                    </div>
                </div>
                
                <div class="overflow-x-auto w-full">
                    <table class="w-full text-left border-collapse min-w-max">
                        <thead class="sticky top-0 shadow-sm z-10">
                            <tr>
                                <th class="px-4 py-4 border-b border-slate-200 text-xs font-bold text-slate-600 bg-slate-50 w-16">Node</th>
                                <th class="px-4 py-4 border-b border-slate-200 text-xs font-bold text-slate-600 bg-slate-50">Hardware Name</th>
                                <th class="px-4 py-4 border-b border-slate-200 text-xs font-bold text-slate-600 bg-slate-50">Category</th>
                                {date_headers}
                            </tr>
                        </thead>
                        <tbody class="divide-y divide-slate-100">
                            {rows_html}
                        </tbody>
                    </table>
                </div>
            </section>
        </div>
    </body>
    </html>
    """

    with open(report_file, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"✅ Success! 7-Day Continuity Matrix generated at: {report_file}")
    webbrowser.open(Path(report_file).resolve().as_uri())

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=str, default="260201", help="Start date (YYMMDD)")
    parser.add_argument("--days", type=int, default=7, help="Number of consecutive days to check")
    args = parser.parse_args()
    main(args.start, args.days)