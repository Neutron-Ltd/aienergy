"""
AIENERGY Energy Balance Report
==============================
Computes: (Grid Import + Solar) - (Consumption + Grid Export) for balance; full data quality statistics.

Data sources (matching griddata.py, solardata.py, endmetersuse.py):
- Grid:  site_energy_{YYMMDD}, meter 1, ImpEP/ExpEP odometer deltas
- Solar: site_power_{YYMMDD}, PV nodes from relation.json, Pt integration
- Consumption: site_power_{YYMMDD}, end meters (leaf nodes) excluding 56,29,31,30
"""

import os
import argparse
import webbrowser
from pathlib import Path
import mysql.connector
import pandas as pd
import json
from datetime import datetime, timedelta
from collections import defaultdict

# --- CONFIGURATION ---
DB_CONFIG = {
    "host": "localhost",
    "port": 3307,
    "user": "energy_user",
    "password": "REDACTED",
    "database": "unified"
}

EXCLUDED_IDS = [56, 29, 31, 30]  # Solar + Storage (same as endmetersuse.py)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_FILE = os.path.join(SCRIPT_DIR, "..", "relation.json")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "data")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def get_node_name_from_json(target_id):
    """Extract node name from relation.json."""
    for path in [os.path.join(SCRIPT_DIR, "relation.json"), JSON_FILE]:
        if not os.path.exists(path):
            continue
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            def find_name(node, tid):
                if node.get("id") == tid:
                    return node.get("name")
                for sub in node.get("sub_nodes", []):
                    res = find_name(sub, tid)
                    if res:
                        return res
                return None
            return find_name(data.get("root_node", {}), target_id) or f"Node {target_id}"
        except Exception:
            pass
    return f"Node {target_id}"


def get_renewable_nodes(node, target_list=None):
    """PV/Solar nodes from relation.json (same logic as solardata.py)."""
    if target_list is None:
        target_list = []
    category = node.get("category", "").lower()
    if any(k in category for k in ["pv", "solar"]):
        if "id" in node:
            target_list.append({"id": node["id"], "name": node.get("name", f"Node {node['id']}")})
    for sub in node.get("sub_nodes", []):
        get_renewable_nodes(sub, target_list)
    return target_list


def get_end_meters(node, end_meter_list=None):
    """Leaf nodes excluding EXCLUDED_IDS (same as endmetersuse.py)."""
    if end_meter_list is None:
        end_meter_list = []
    sub_nodes = node.get("sub_nodes", [])
    node_id = node.get("id")
    if not sub_nodes:
        if node_id is not None and node_id not in EXCLUDED_IDS:
            end_meter_list.append({"id": node_id, "name": node.get("name", f"Node {node_id}")})
    else:
        for sub in sub_nodes:
            get_end_meters(sub, end_meter_list)
    return end_meter_list


def generate_date_range(start_date_str, days=7):
    """YYMMDD list for the period."""
    start_dt = datetime.strptime(f"20{start_date_str}", "%Y%m%d")
    return [(start_dt + timedelta(days=i)).strftime("%y%m%d") for i in range(days)]


def fetch_grid_data(cursor, dates, target_id=1):
    """Fetch grid import/export using same odometer-delta logic as griddata.py."""
    cursor.execute("SELECT ratio FROM meter_info WHERE meter_number = %s", (target_id,))
    ratio_res = cursor.fetchone()
    ratio = ratio_res['ratio'] if ratio_res else 2000

    daily = []
    total_import = 0
    total_export = 0
    total_act = 0
    total_exp = 0
    intervals = []
    gap_days = []

    for date_str in dates:
        table = f"site_energy_{date_str}"
        try:
            cursor.execute(
                f"SELECT ts, ImpEP, ExpEP FROM {table} WHERE meter_number = %s ORDER BY ts ASC",
                (target_id,)
            )
            rows = cursor.fetchall()
            df = pd.DataFrame(rows)
            if df.empty:
                daily.append({"import": 0, "export": 0, "act": 0, "exp": 96, "health": 0, "interval_min": None})
                gap_days.append(date_str)
                continue

            df['ts'] = pd.to_datetime(df['ts'])
            median_sec = df['ts'].diff().dt.total_seconds().median()
            median_min = int(median_sec / 60) if median_sec and not pd.isna(median_sec) else 15
            if median_min <= 0:
                median_min = 15
            intervals.append(median_min)

            exp_count = 1440 // median_min
            act_count = len(df)
            health = min(100.0, (act_count / exp_count) * 100) if exp_count > 0 else 0

            imp_delta = (df['ImpEP'].iloc[-1] - df['ImpEP'].iloc[0]) * (ratio / 1000.0)
            exp_delta = (df['ExpEP'].iloc[-1] - df['ExpEP'].iloc[0]) * (ratio / 1000.0)

            total_import += imp_delta
            total_export += exp_delta
            total_act += act_count
            total_exp += exp_count

            if health < 95:
                gap_days.append(date_str)

            daily.append({
                "import": imp_delta,
                "export": exp_delta,
                "act": act_count,
                "exp": exp_count,
                "health": health,
                "interval_min": median_min,
            })
        except Exception:
            daily.append({"import": 0, "export": 0, "act": 0, "exp": 96, "health": 0, "interval_min": None})
            gap_days.append(date_str)

    median_int = int(pd.Series(intervals).median()) if intervals else 15
    overall_health = min(100.0, (total_act / total_exp) * 100) if total_exp > 0 else 0

    return {
        "total_import_kwh": total_import,
        "total_export_kwh": total_export,
        "daily": daily,
        "median_interval_min": median_int,
        "overall_health": overall_health,
        "total_act": total_act,
        "total_exp": total_exp,
        "gap_days": gap_days,
        "ratio": ratio,
    }


def fetch_power_based_data(cursor, dates, node_list, table_prefix="site_power"):
    """
    Fetch data from site_power_* using Pt integration (same as solardata/endmetersuse).
    Returns per-day totals and statistics.
    """
    daily_totals = {d: 0.0 for d in dates}
    total_act = 0
    total_exp = 0
    intervals = []
    gap_days = set()
    per_node_stats = defaultdict(lambda: {"act": 0, "exp": 0, "health_days": []})

    for date_str in dates:
        table = f"{table_prefix}_{date_str}"
        day_total = 0.0
        day_act = 0
        day_exp = 0
        day_intervals = []

        for node in node_list:
            nid = node['id']
            cursor.execute("SELECT ratio FROM meter_info WHERE meter_number = %s", (nid,))
            r = cursor.fetchone()
            ratio = r['ratio'] if r else 1

            try:
                cursor.execute(f"SELECT ts, Pt FROM {table} WHERE meter_number = %s ORDER BY ts ASC", (nid,))
                df = pd.DataFrame(cursor.fetchall())
                if df.empty:
                    continue

                df['ts'] = pd.to_datetime(df['ts'])
                median_sec = df['ts'].diff().dt.total_seconds().median()
                median_min = median_sec / 60 if median_sec and not pd.isna(median_sec) else 15
                if median_min <= 0:
                    median_min = 15
                day_intervals.append(median_min)

                daily_kwh = (pd.to_numeric(df['Pt']).sum() * ratio) * (median_min / 60.0)
                exp_count = int(1440 // median_min)
                act_count = len(df)
                health = (act_count / exp_count) * 100 if exp_count > 0 else 0

                day_total += daily_kwh
                day_act += act_count
                day_exp += exp_count
                per_node_stats[nid]["act"] += act_count
                per_node_stats[nid]["exp"] += exp_count
                if health < 95:
                    per_node_stats[nid]["health_days"].append(date_str)

            except Exception:
                pass

        daily_totals[date_str] = day_total
        total_act += day_act
        total_exp += day_exp
        intervals.extend(day_intervals)
        day_health = (day_act / day_exp * 100) if day_exp > 0 else 0
        if day_health < 95 and day_exp > 0:
            gap_days.add(date_str)

    median_int = int(pd.Series(intervals).median()) if intervals else 15
    overall_health = min(100.0, (total_act / total_exp) * 100) if total_exp > 0 else 0

    return {
        "daily_totals": daily_totals,
        "total_kwh": sum(daily_totals.values()),
        "median_interval_min": median_int,
        "overall_health": overall_health,
        "total_act": total_act,
        "total_exp": total_exp,
        "gap_days": sorted(gap_days),
        "per_node_stats": dict(per_node_stats),
    }


def main(start_date, days, target_grid_id=1, open_browser=True):
    dates = generate_date_range(start_date, days)
    end_date_str = dates[-1]
    report_file = os.path.join(OUTPUT_DIR, f"report-{start_date}-{days}d.html")
    display_title = get_node_name_from_json(target_grid_id)

    try:
        with open(JSON_FILE, 'r', encoding='utf-8') as f:
            tree = json.load(f)
        root = tree.get("root_node", {})
        solar_nodes = get_renewable_nodes(root)
        end_meters = get_end_meters(root)
    except Exception as e:
        print(f"❌ JSON Error: {e}")
        return

    print(f"⚡ Generating Energy Balance Report ({days}d, 20{start_date} - 20{end_date_str})...")

    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)

        grid = fetch_grid_data(cursor, dates, target_grid_id)
        solar = fetch_power_based_data(cursor, dates, solar_nodes)
        consumption = fetch_power_based_data(cursor, dates, end_meters)

        conn.close()
    except Exception as e:
        print(f"❌ DB Error: {e}")
        return

    # Energy balance: (Grid Import + Solar) - (Consumption + Grid Export)
    supply = grid["total_import_kwh"] + solar["total_kwh"]
    consumption_total = consumption["total_kwh"]
    grid_export_total = grid["total_export_kwh"]
    loads_out = consumption_total + grid_export_total
    energy_diff = supply - loads_out
    pct_diff = (energy_diff / loads_out * 100) if loads_out > 0 else 0

    # Build daily rows for the table
    daily_rows = []
    for i, d in enumerate(dates):
        g = grid["daily"][i]
        s_kwh = solar["daily_totals"][d]
        c_kwh = consumption["daily_totals"][d]
        g_exp = g.get("export", 0) or 0
        day_supply = g["import"] + s_kwh
        day_diff = day_supply - c_kwh - g_exp
        daily_rows.append({
            "date": f"20{d[0:2]}-{d[2:4]}-{d[4:6]}",
            "grid_import": g["import"],
            "solar": s_kwh,
            "supply": day_supply,
            "consumption": c_kwh,
            "grid_export": g_exp,
            "diff": day_diff,
            "grid_health": g["health"],
            "grid_interval": g.get("interval_min"),
        })

    # HTML generation
    daily_html = ""
    for r in daily_rows:
        diff_cls = "text-emerald-600" if r["diff"] >= 0 else "text-rose-600"
        daily_html += f"""
        <tr class="hover:bg-slate-50 border-b border-slate-100">
            <td class="p-3 font-mono text-sm font-bold text-slate-700">{r['date']}</td>
            <td class="p-3 text-right font-mono text-xs text-emerald-700">{r['grid_import']:,.1f}</td>
            <td class="p-3 text-right font-mono text-xs text-amber-600">{r['solar']:,.1f}</td>
            <td class="p-3 text-right font-mono text-xs text-indigo-700">{r['consumption']:,.1f}</td>
            <td class="p-3 text-right font-mono text-xs text-blue-700">{r['grid_export']:,.1f}</td>
            <td class="p-3 text-right font-mono text-xs font-bold text-slate-800">{r['supply']:,.1f}</td>
            <td class="p-3 text-right font-mono text-xs font-bold {diff_cls}">{r['diff']:+,.1f}</td>
            <td class="p-3 text-center text-[10px] font-mono">{r['grid_interval'] or '—'}</td>
            <td class="p-3 text-center text-[10px] font-bold {'text-emerald-600' if r['grid_health']>=95 else 'text-amber-600' if r['grid_health']>0 else 'text-rose-600'}">{r['grid_health']:.0f}%</td>
        </tr>
        """

    grid_gaps_str = ", ".join(grid["gap_days"]) if grid["gap_days"] else "None / 无"
    solar_gaps_str = ", ".join(solar["gap_days"]) if solar["gap_days"] else "None / 无"
    cons_gaps_str = ", ".join(consumption["gap_days"]) if consumption["gap_days"] else "None / 无"

    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Energy Balance Report / 能源平衡报告 | AIENERGY</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            ::-webkit-scrollbar {{ width: 8px; height: 8px; }}
            ::-webkit-scrollbar-track {{ background: #f8fafc; }}
            ::-webkit-scrollbar-thumb {{ background: #cbd5e1; border-radius: 4px; }}
            @media print {{ @page {{ size: landscape; margin: 10mm; }} body {{ background: white; }} .shadow-sm, .shadow-xl {{ box-shadow: none !important; border: 1px solid #e2e8f0 !important; }} }}
        </style>
    </head>
    <body class="bg-slate-100 min-h-screen font-sans text-slate-900 pb-12">
        <div class="max-w-[1800px] mx-auto mt-8 px-6 space-y-8">
            <header class="bg-white p-8 rounded-2xl shadow-sm border border-slate-200">
                <h1 class="text-3xl font-black text-slate-900">Energy Balance Report / 能源平衡报告</h1>
                <p class="text-slate-500 font-medium italic mt-1">
                    Formula: (Grid Import + Solar) − (Consumption + Grid Export) / 公式:(电网输入+太阳能)−(用电+电网输出) | {days}-Day Summary / {days}天汇总 | 20{start_date} → 20{end_date_str}
                </p>
                <p class="text-slate-400 text-xs mt-2 font-mono">
                    Grid / 电网: {display_title} (ID:{target_grid_id}) • Solar / 太阳能: {len(solar_nodes)} nodes / 节点 • Consumption / 用电: {len(end_meters)} end meters / 末端电表
                </p>
            </header>

            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4">
                <div class="bg-emerald-900 p-6 rounded-2xl shadow-xl text-white">
                    <span class="text-[10px] font-bold text-emerald-300 uppercase tracking-widest">Grid Import / 电网输入</span>
                    <div class="text-2xl font-black mt-1">{grid['total_import_kwh']:,.1f} <span class="text-sm font-light">kWh</span></div>
                </div>
                <div class="bg-amber-600 p-6 rounded-2xl shadow-xl text-white">
                    <span class="text-[10px] font-bold text-amber-200 uppercase tracking-widest">Solar / 太阳能</span>
                    <div class="text-2xl font-black mt-1">{solar['total_kwh']:,.1f} <span class="text-sm font-light">kWh</span></div>
                </div>
                <div class="bg-indigo-900 p-6 rounded-2xl shadow-xl text-white">
                    <span class="text-[10px] font-bold text-indigo-300 uppercase tracking-widest">Consumption / 用电量</span>
                    <div class="text-2xl font-black mt-1">{consumption_total:,.1f} <span class="text-sm font-light">kWh</span></div>
                </div>
                <div class="bg-blue-900 p-6 rounded-2xl shadow-xl text-white">
                    <span class="text-[10px] font-bold text-blue-300 uppercase tracking-widest">Grid Export / 电网输出</span>
                    <div class="text-2xl font-black mt-1">{grid_export_total:,.1f} <span class="text-sm font-light">kWh</span></div>
                </div>
                <div class="bg-slate-800 p-6 rounded-2xl shadow-xl text-white">
                    <span class="text-[10px] font-bold text-slate-300 uppercase tracking-widest">Energy Difference / 能源差值</span>
                    <div class="text-2xl font-black mt-1 {'text-emerald-300' if energy_diff >= 0 else 'text-rose-300'}">{energy_diff:+,.1f} <span class="text-sm font-light">kWh</span></div>
                    <div class="text-xs text-slate-400 mt-1">({pct_diff:+.1f}% of Cons.+Exp. / 占用电+输出)</div>
                </div>
            </div>

            <section class="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden">
                <div class="bg-slate-800 p-4 border-b border-slate-700 text-white">
                    <h3 class="font-bold text-xs uppercase tracking-widest">Data Quality & Frequency / 数据质量与频率</h3>
                </div>
                <div class="p-4 overflow-x-auto">
                    <table class="w-full text-sm">
                        <thead class="text-slate-500 border-b">
                            <tr>
                                <th class="p-2 text-left font-bold">Source / 来源</th>
                                <th class="p-2 text-right font-bold">DB Table / 数据表</th>
                                <th class="p-2 text-right font-bold">Data Freq (median) / 数据频率(中位)</th>
                                <th class="p-2 text-right font-bold">Expected Logs / 预期日志</th>
                                <th class="p-2 text-right font-bold">Actual Logs / 实际日志</th>
                                <th class="p-2 text-right font-bold">Reliability / 可靠性</th>
                                <th class="p-2 text-left font-bold">Gap Days (&lt;95%) / 缺口日(&lt;95%)</th>
                            </tr>
                        </thead>
                        <tbody class="divide-y divide-slate-100">
                            <tr class="hover:bg-slate-50">
                                <td class="p-2 font-bold text-emerald-700">Grid</td>
                                <td class="p-2 font-mono text-[10px]">site_energy_*</td>
                                <td class="p-2 text-right font-mono">{grid['median_interval_min']} min</td>
                                <td class="p-2 text-right font-mono">{grid['total_exp']:,}</td>
                                <td class="p-2 text-right font-mono">{grid['total_act']:,}</td>
                                <td class="p-2 text-right font-bold {'text-emerald-600' if grid['overall_health']>=95 else 'text-amber-600'}">{grid['overall_health']:.1f}%</td>
                                <td class="p-2 font-mono text-[10px] text-slate-600">{grid_gaps_str}</td>
                            </tr>
                            <tr class="hover:bg-slate-50">
                                <td class="p-2 font-bold text-amber-600">Solar</td>
                                <td class="p-2 font-mono text-[10px]">site_power_*</td>
                                <td class="p-2 text-right font-mono">{solar['median_interval_min']} min</td>
                                <td class="p-2 text-right font-mono">{solar['total_exp']:,}</td>
                                <td class="p-2 text-right font-mono">{solar['total_act']:,}</td>
                                <td class="p-2 text-right font-bold {'text-emerald-600' if solar['overall_health']>=95 else 'text-amber-600'}">{solar['overall_health']:.1f}%</td>
                                <td class="p-2 font-mono text-[10px] text-slate-600">{solar_gaps_str}</td>
                            </tr>
                            <tr class="hover:bg-slate-50">
                                <td class="p-2 font-bold text-indigo-700">Consumption</td>
                                <td class="p-2 font-mono text-[10px]">site_power_*</td>
                                <td class="p-2 text-right font-mono">{consumption['median_interval_min']} min</td>
                                <td class="p-2 text-right font-mono">{consumption['total_exp']:,}</td>
                                <td class="p-2 text-right font-mono">{consumption['total_act']:,}</td>
                                <td class="p-2 text-right font-bold {'text-emerald-600' if consumption['overall_health']>=95 else 'text-amber-600'}">{consumption['overall_health']:.1f}%</td>
                                <td class="p-2 font-mono text-[10px] text-slate-600">{cons_gaps_str}</td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </section>

            <section class="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden">
                <div class="bg-slate-800 p-4 border-b border-slate-700 text-white flex justify-between items-center">
                    <h3 class="font-bold text-xs uppercase tracking-widest">Daily Energy Balance / 日能源平衡</h3>
                    <span class="text-[10px] text-slate-400 font-mono">Supply = Grid Import + Solar / 供应=电网输入+太阳能 | Diff = Supply − (Consumption + Grid Export) / 差值=供应−(用电+电网输出)</span>
                </div>
                <div class="overflow-x-auto">
                    <table class="w-full text-sm">
                        <thead class="bg-slate-50 text-slate-500 border-b">
                            <tr>
                                <th class="p-3 font-bold text-left">Date / 日期</th>
                                <th class="p-3 font-bold text-right text-emerald-700">Grid Import / 电网输入</th>
                                <th class="p-3 font-bold text-right text-amber-600">Solar / 太阳能</th>
                                <th class="p-3 font-bold text-right text-indigo-700">Consumption / 用电</th>
                                <th class="p-3 font-bold text-right text-blue-700">Grid Export / 电网输出</th>
                                <th class="p-3 font-bold text-right">Supply / 供应</th>
                                <th class="p-3 font-bold text-right">Diff (kWh) / 差值</th>
                                <th class="p-3 font-bold text-center">Grid Intv / 电网间隔</th>
                                <th class="p-3 font-bold text-center">Grid Health / 电网健康</th>
                            </tr>
                        </thead>
                        <tbody>{daily_html}</tbody>
                    </table>
                </div>
            </section>

            <section class="bg-white rounded-2xl shadow-sm border border-slate-200 p-6">
                <h3 class="font-bold text-xs uppercase tracking-widest text-slate-500 mb-3">Calculation Notes / 计算说明</h3>
                <ul class="text-xs text-slate-600 space-y-1 font-mono">
                    <li>• Grid / 电网: Odometer delta (ImpEP/ExpEP last − first) × (ratio/1000) from site_energy_*</li>
                    <li>• Solar / 太阳能: Σ(Pt) × ratio × (interval_min/60) from site_power_* for PV nodes / 光伏节点</li>
                    <li>• Consumption / 用电: Σ(Pt) × ratio × (interval_min/60) from site_power_* for end meters (excl. 56,29,31,30) / 末端电表(不含56,29,31,30)</li>
                    <li>• Energy Diff = (Grid Import + Solar) − (Consumption + Grid Export) / 能源差值=(电网输入+太阳能)−(用电+电网输出)</li>
                    <li>• Diff &gt; 0: supply exceeds measured loads+export / 供应大于用电与输出之和; &lt; 0: shortfall or gaps / 亏空或测量缺口</li>
                </ul>
            </section>

            <footer class="text-center text-slate-400 text-[10px] py-8 border-t border-slate-100">
                Generated by AIENERGY System / AI能源系统生成 • Energy Balance Report / 能源平衡报告
            </footer>
        </div>
    </body>
    </html>
    """

    with open(report_file, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"✅ Energy Balance Report: {report_file}")
    if open_browser:
        webbrowser.open(Path(report_file).resolve().as_uri())
    return report_file


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=str, default="260201", help="Start date (YYMMDD)")
    parser.add_argument("--days", type=int, default=7, help="Number of days")
    parser.add_argument("--grid-id", type=int, default=1, help="Grid meter node ID")
    parser.add_argument("--no-browser", action="store_true", help="Do not open browser after generating")
    args = parser.parse_args()
    main(args.start, args.days, args.grid_id, open_browser=not args.no_browser)
