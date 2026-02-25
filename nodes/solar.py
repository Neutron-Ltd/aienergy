import os
import argparse
import webbrowser
from pathlib import Path
import mysql.connector
import pandas as pd
import json
from datetime import datetime, time, timedelta

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

def get_renewable_nodes(node, target_list=None):
    """Recursively searches relation.json for Solar PV nodes only."""
    if target_list is None:
        target_list = []
    
    category = node.get("category", "").lower()
    
    # Identify if the node is a solar PV source
    if "pv" in category or "solar" in category:
        if "id" in node:
            target_list.append({
                "id": node["id"],
                "name": node.get("name", f"Node {node['id']}"),
                "category": category,
                "type": "Solar PV",
                "color": "emerald",
                "icon": "☀️"
            })
            
    for sub in node.get("sub_nodes", []):
        get_renewable_nodes(sub, target_list)
        
    return target_list

def main(date_suffix):
    report_file = os.path.join(OUTPUT_DIR, f"solar-{date_suffix}.html")
    print(f"⚡ Generating Solar Generation Audit for 20{date_suffix}...")

    # 1. Load Topology
    try:
        with open(JSON_FILE, 'r', encoding='utf-8') as f:
            tree_data = json.load(f)
        target_nodes = get_renewable_nodes(tree_data.get("root_node", {}))
        
        if not target_nodes:
            print("❌ No Solar PV nodes found in relation.json.")
            return
    except Exception as e:
        print(f"❌ JSON Error: {e}")
        return

    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
    except Exception as e:
        print(f"❌ DB Connection Error: {e}")
        return

    node_sections_html = ""
    all_logs = []

    # 2. Process each Renewable Node
    for node in target_nodes:
        nid = node['id']
        name = node['name']
        c = node['color']
        
        # Fetch Ratio
        cursor.execute("SELECT ratio FROM meter_info WHERE meter_number = %s", (nid,))
        ratio_res = cursor.fetchone()
        ratio = ratio_res['ratio'] if ratio_res else 1

        # Fetch Pt strictly bounded to 24h
        query = f"""
            SELECT ts, Pt 
            FROM site_power_{date_suffix} 
            WHERE meter_number = %s 
            AND ts >= '20{date_suffix[0:2]}-{date_suffix[2:4]}-{date_suffix[4:6]} 00:00:00'
            AND ts <= '20{date_suffix[0:2]}-{date_suffix[2:4]}-{date_suffix[4:6]} 23:59:59'
            ORDER BY ts ASC
        """
        cursor.execute(query, (nid,))
        rows = cursor.fetchall()
        df = pd.DataFrame(rows)

        # --- MATH & CONTINUITY LOGIC ---
        missing_slots_text = "Perfect Coverage"
        if not df.empty:
            df['ts'] = pd.to_datetime(df['ts'])
            target_date_obj = datetime.strptime(f"20{date_suffix}", "%Y%m%d").date()
            
            # Filter out NaN/None in Pt
            df['Pt'] = pd.to_numeric(df['Pt'], errors='coerce').fillna(0)
            
            median_interval = int(df['ts'].diff().dt.total_seconds().median() / 60) if len(df) > 1 else 15
            if median_interval == 0: median_interval = 15
                
            expected_count = 1440 // median_interval
            actual_count = len(df)
            reliability = min(100.0, (actual_count / expected_count) * 100)
            
            # Missing Slot Detection
            expected_times = [datetime.combine(target_date_obj, time(0,0)) + timedelta(minutes=median_interval*i) for i in range(expected_count)]
            actual_rounded = df['ts'].dt.round(f'{median_interval}min')
            missing_times = [et for et in expected_times if et not in actual_rounded.values]
            
            if missing_times:
                missing_strs = [mt.strftime('%H:%M') for mt in missing_times]
                if "00:00" in missing_strs:
                    missing_slots_text = "00:00 (Crossover)"
                    missing_strs.remove("00:00")
                if missing_strs:
                    missing_slots_text += ", " + ", ".join(missing_strs[:2]) + ("..." if len(missing_strs) > 2 else "")
            else:
                missing_slots_text = "None"

            # INTEGRATION MATH: Power -> Energy
            df['Real_Pt'] = df['Pt'] * ratio
            df['Interval_kWh'] = df['Real_Pt'] * (median_interval / 60.0)
            
            daily_kwh = df['Interval_kWh'].sum()
            kwh_display = f"{daily_kwh:,.1f}"
            kwh_label = "Daily Generation"
            kwh_desc = f"Absolute Yield × {ratio}"

            # Prepare for unified table
            df['node_name'] = name
            df['node_id'] = nid
            df['median_interval'] = median_interval
            all_logs.append(df)
        else:
            median_interval = expected_count = actual_count = reliability = 0
            kwh_display = "0.0"
            kwh_label = "Daily Data"
            kwh_desc = "No logs available"
            missing_slots_text = "All Data Missing"

        # --- RENDER NODE DASHBOARD ---
        node_sections_html += f"""
        <div class="mb-10">
            <header class="bg-white p-6 rounded-t-2xl shadow-sm border border-slate-200 border-b-0 flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
                <div>
                    <h2 class="text-xl font-bold text-slate-900 flex items-center gap-2">{node['icon']} {name}</h2>
                    <p class="text-slate-500 text-xs font-medium uppercase tracking-widest mt-1">{node['type']} Integration Audit</p>
                </div>
                <div class="bg-slate-100 p-2 rounded border border-slate-200">
                    <div class="text-[10px] font-bold text-slate-400 uppercase tracking-widest text-right">Hardware</div>
                    <div class="text-sm font-mono font-bold text-blue-600">ID: {nid} <span class="text-slate-400 mx-1">|</span> Ratio: {ratio}</div>
                </div>
            </header>

            <div class="grid grid-cols-1 md:grid-cols-4 gap-0 shadow-sm border border-slate-200 rounded-b-2xl overflow-hidden">
                <div class="bg-white p-5 border-r border-slate-100">
                    <span class="text-[10px] font-bold text-slate-400 uppercase">Reliability Score</span>
                    <div class="text-2xl font-black text-slate-800 mt-1">{reliability:.1f}%</div>
                    <div class="w-full bg-slate-100 h-1.5 rounded-full mt-3 overflow-hidden">
                        <div class="bg-{c}-500 h-full transition-all" style="width: {reliability}%"></div>
                    </div>
                </div>
                <div class="bg-white p-5 border-r border-slate-100">
                    <span class="text-[10px] font-bold text-slate-400 uppercase">Logs Captured</span>
                    <div class="text-2xl font-black text-slate-800 mt-1">{actual_count} <span class="text-slate-300 text-lg">/ {expected_count}</span></div>
                    <p class="text-[10px] mt-1 {'text-emerald-600 font-bold' if actual_count == expected_count else 'text-red-500 font-bold'}">
                        Missing: {missing_slots_text}
                    </p>
                </div>
                <div class="bg-white p-5 border-r border-slate-100">
                    <span class="text-[10px] font-bold text-slate-400 uppercase">Interval Block</span>
                    <div class="text-2xl font-black text-slate-800 mt-1">{median_interval} <span class="text-base text-slate-400 font-normal">min</span></div>
                    <span class="text-[10px] text-slate-400 font-medium">Integration timeframe</span>
                </div>
                <div class="bg-slate-900 p-5 text-white">
                    <span class="text-[10px] font-bold text-{c}-300 uppercase">{kwh_label}</span>
                    <div class="text-2xl font-black mt-1 leading-tight">{kwh_display} <span class="text-xs font-light text-slate-400">{'kWh' if 'C:' not in kwh_display else ''}</span></div>
                    <span class="text-[10px] text-slate-500 italic block mt-1">{kwh_desc}</span>
                </div>
            </div>
        </div>
        """

    conn.close()

    # --- MASTER LOG TABLE ---
    rows_html = ""
    if all_logs:
        master_df = pd.concat(all_logs).sort_values(by=['ts', 'node_id'], ascending=[False, True])
        
        # Build Gap Map
        prev_ts_map = {}
        for _, r in master_df[::-1].iterrows(): # Reverse iterate to track gaps per node
            nid = r['node_id']
            gap_badge = ""
            if nid in prev_ts_map:
                diff = (r['ts'] - prev_ts_map[nid]).total_seconds() / 60
                if diff > (r['median_interval'] * 1.5):
                    gap_badge = f"<span class='ml-2 text-[10px] bg-red-100 text-red-600 px-1.5 py-0.5 rounded border border-red-200'>Gap: {int(diff)}m</span>"
            prev_ts_map[nid] = r['ts']

            # Power Formatting (We know it's always positive now)
            power_color = "text-emerald-600 font-bold" if r['Real_Pt'] > 0 else "text-slate-400"
            energy_sign = r['Interval_kWh']
            
            rows_html = f"""
            <tr class="hover:bg-slate-50 border-b border-slate-100 transition-colors bg-white">
                <td class="p-3 font-mono text-xs text-slate-500 whitespace-nowrap">{r['ts'].strftime('%H:%M:%S')} {gap_badge}</td>
                <td class="p-3 text-xs text-slate-700 font-medium">{r['node_name']}</td>
                <td class="p-3 text-right font-mono text-slate-400">{r['Pt']:,.3f}</td>
                <td class="p-3 text-right {power_color}">{r['Real_Pt']:,.1f} kW</td>
                <td class="p-3 text-right font-bold text-emerald-800 bg-emerald-50/30">{energy_sign:,.2f} kWh</td>
            </tr>""" + rows_html

    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8"><title>Solar Generation - 20{date_suffix}</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            ::-webkit-scrollbar {{ width: 8px; height: 8px; }}
            ::-webkit-scrollbar-track {{ background: #f8fafc; }}
            ::-webkit-scrollbar-thumb {{ background: #cbd5e1; border-radius: 4px; }}
            ::-webkit-scrollbar-thumb:hover {{ background: #94a3b8; }}
            @media print {{
                @page {{ size: landscape; margin: 10mm; }}
                body {{ background: white; }}
                .shadow-sm {{ box-shadow: none; border-color: #e2e8f0; }}
            }}
        </style>
    </head>
    <body class="bg-slate-100 min-h-screen font-sans text-slate-900 pb-12">
        <div class="max-w-[1600px] mx-auto mt-8 px-6 space-y-8">
            
            <header class="bg-white p-8 rounded-2xl shadow-sm border border-slate-200 flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
                <div>
                    <h1 class="text-3xl font-black text-slate-900">Solar Generation Audit</h1>
                    <p class="text-slate-500 font-medium italic mt-1">Active Power (Pt) Integration Audit | Target: <span class="font-mono text-slate-700">20{date_suffix}</span></p>
                </div>
            </header>

            {node_sections_html}

            <section class="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden mt-12">
                <div class="bg-slate-900 p-4 text-white border-b border-slate-700 rounded-t-2xl flex justify-between items-center">
                    <h3 class="font-bold text-xs uppercase tracking-widest">Unified Integration Log</h3>
                    <span class="text-[10px] font-mono text-slate-400">Formula: Pt × Ratio × (Interval/60)</span>
                </div>
                
                <div class="w-full">
                    <table class="w-full text-sm text-left">
                        <thead class="bg-slate-50 text-slate-500 border-b border-slate-200">
                            <tr>
                                <th class="p-4 font-bold">Sync Time</th>
                                <th class="p-4 font-bold">Node Hardware</th>
                                <th class="p-4 font-bold text-right">Raw Pt</th>
                                <th class="p-4 font-bold text-right">Actual Power (kW)</th>
                                <th class="p-4 text-right font-bold">Integrated Yield (kWh)</th>
                            </tr>
                        </thead>
                        <tbody class="divide-y divide-slate-100">
                            {rows_html if rows_html else "<tr><td colspan='5' class='p-12 text-center text-slate-400'>No solar generation data found within the 24-hour boundary.</td></tr>"}
                        </tbody>
                    </table>
                </div>
            </section>
            
            <footer class="text-center text-slate-400 text-xs py-8">
                Generated by AIENERGY System • Print optimized
            </footer>
        </div>
    </body>
    </html>
    """
    
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"✅ Success: Solar Generation Audit for 20{date_suffix} generated at {report_file}")
    webbrowser.open(Path(report_file).resolve().as_uri())

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default="260201", help="Target date (YYMMDD)")
    main(parser.parse_args().date)