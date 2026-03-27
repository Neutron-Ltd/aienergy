import os
import sys
import argparse
import webbrowser
from pathlib import Path
import json
from datetime import datetime, timedelta

import mysql.connector
import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from db.mysql_env import mysql_connect_kwargs
from model.ml_predictor import NodeAwareMLPredictor
from model.topology_engine import TopologyEngine

# IDs to explicitly exclude from end-meter reports (Solar and Storage)
EXCLUDED_IDS = [56, 29, 31, 30]
DB_CONFIG = mysql_connect_kwargs()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_FILE = os.path.join(SCRIPT_DIR, "..", "relation.json")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "data")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def get_end_meters(node, end_meter_list=None):
    """Recursively extracts ONLY leaf nodes (no sub_nodes), excluding IDs above."""
    if end_meter_list is None:
        end_meter_list = []

    sub_nodes = node.get("sub_nodes", [])
    node_id = node.get("id")

    if not sub_nodes:
        if node_id is not None and node_id not in EXCLUDED_IDS:
            end_meter_list.append({
                "id": int(node_id),
                "name": node.get("name", f"Node {node_id}"),
                "category": node.get("category", "Unknown"),
            })
    else:
        for sub in sub_nodes:
            get_end_meters(sub, end_meter_list)
    return end_meter_list


def kwh_from_15min_grid(series: pd.Series) -> float:
    """Fixed-interval kWh integration for 15-minute Pt grid."""
    if series.empty:
        return 0.0
    return float(pd.to_numeric(series, errors="coerce").fillna(0.0).sum() * (15.0 / 60.0))


def to_15min_actual_grid(raw_df: pd.DataFrame, target_index: pd.DatetimeIndex, ratio: float) -> pd.Series:
    """Resample raw meter Pt into strict 15-minute mean slots, NaN if empty."""
    if raw_df.empty:
        return pd.Series(index=target_index, dtype=float)

    tmp = raw_df.copy()
    tmp["ts"] = pd.to_datetime(tmp["ts"], errors="coerce")
    tmp["Pt"] = pd.to_numeric(tmp["Pt"], errors="coerce") * float(ratio)
    tmp = tmp.dropna(subset=["ts"]).set_index("ts").sort_index()
    if tmp.empty:
        return pd.Series(index=target_index, dtype=float)

    # Mean within each 15-minute slot; empty slots remain NaN after reindex.
    slot_mean = tmp["Pt"].resample("15min").mean()
    return slot_mean.reindex(target_index)


def generate_date_range(start_date_str, days=7):
    start_dt = datetime.strptime(f"20{start_date_str}", "%Y%m%d")
    return [(start_dt + timedelta(days=i)).strftime("%y%m%d") for i in range(days)]


def main(start_date, days=7, model_dir=None, open_browser=True):
    report_file = os.path.join(OUTPUT_DIR, f"endmetersai-{start_date}-{days}d.html")
    print(f"Starting End Meter AI Prediction for 20{start_date}, {days} day(s)...")
    artifacts_dir = Path(model_dir).resolve() if model_dir else (_ROOT / "model_artifacts").resolve()
    os.environ["MODEL_ARTIFACT_DIR"] = str(artifacts_dir)
    print(f"Model artifacts directory: {artifacts_dir}")
    dates = generate_date_range(start_date, days)

    try:
        with open(JSON_FILE, "r", encoding="utf-8") as f:
            tree_data = json.load(f)
        end_nodes = get_end_meters(tree_data.get("root_node", {}))
    except Exception as e:
        print(f"❌ JSON Error: {e}")
        return

    if not end_nodes:
        print("❌ No valid end meters found after filtering.")
        return

    try:
        topology = TopologyEngine(Path(JSON_FILE))
        predictor = NodeAwareMLPredictor(topology=topology)
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
    except Exception as e:
        print(f"❌ Initialization Error: {e}")
        return

    rows = []
    total_actual_kwh = 0.0          # sum of available actual-only slots
    total_ai_recovered_kwh = 0.0    # sum of AI only on missing slots
    total_blended_kwh = 0.0         # actual.fillna(predicted)
    loaded_models = 0

    model_cache = {}
    for node in end_nodes:
        nid = node["id"]
        actual_kwh = 0.0
        recovered_kwh = 0.0
        blended_kwh = 0.0
        total_slots = 0
        present_slots = 0
        model_state = "missing"

        if nid not in model_cache:
            try:
                model_cache[nid] = predictor.load_trained_model(nid)
            except Exception:
                model_cache[nid] = False
        model_loaded = bool(model_cache[nid])
        if model_loaded:
            loaded_models += 1

        for date_suffix in dates:
            day_start = datetime.strptime(f"20{date_suffix}", "%Y%m%d").replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day_start.replace(hour=23, minute=59, second=59)
            target_index = pd.date_range(start=day_start, end=day_end, freq="15min")
            table = f"site_power_{date_suffix}"

            # Main grid context sampled to daily 15-minute index.
            try:
                cursor.execute(f"SELECT ts, Pt FROM {table} WHERE meter_number = %s ORDER BY ts ASC", (1,))
                root_df = pd.DataFrame(cursor.fetchall())
                main_grid_load = None
                if not root_df.empty:
                    root_df["ts"] = pd.to_datetime(root_df["ts"])
                    root_series = pd.Series(pd.to_numeric(root_df["Pt"], errors="coerce").values, index=root_df["ts"]).sort_index()
                    main_grid_load = root_series.reindex(target_index).ffill().fillna(0.0)
            except Exception:
                main_grid_load = None

            try:
                cursor.execute("SELECT ratio FROM meter_info WHERE meter_number = %s", (nid,))
                ratio_row = cursor.fetchone()
                ratio = float(ratio_row["ratio"]) if ratio_row and ratio_row.get("ratio") is not None else 1.0

                cursor.execute(f"SELECT ts, Pt FROM {table} WHERE meter_number = %s ORDER BY ts ASC", (nid,))
                raw_df = pd.DataFrame(cursor.fetchall())
                actual_grid = to_15min_actual_grid(raw_df, target_index=target_index, ratio=ratio)
            except Exception:
                actual_grid = pd.Series(index=target_index, dtype=float)

            total_slots += len(actual_grid.index)
            present_slots += int(actual_grid.notna().sum())

            day_recovered_kwh = 0.0
            day_blended_kwh = kwh_from_15min_grid(actual_grid)
            day_model_state = "missing"
            if model_loaded:
                try:
                    pred_series = predictor.predict(
                        node_id=nid,
                        target_index=target_index,
                        main_grid_load=main_grid_load,
                        neighboring_pv=None,
                    )
                    pred_series = pd.to_numeric(pred_series, errors="coerce")
                    missing_mask = actual_grid.isna()
                    recovered_series = pred_series.where(missing_mask, 0.0)
                    blended_series = actual_grid.fillna(pred_series)
                    day_recovered_kwh = kwh_from_15min_grid(recovered_series)
                    day_blended_kwh = kwh_from_15min_grid(blended_series)
                    day_model_state = "ok"
                except Exception:
                    day_model_state = "error"

            actual_kwh += kwh_from_15min_grid(actual_grid.fillna(0.0))
            recovered_kwh += day_recovered_kwh
            blended_kwh += day_blended_kwh
            if day_model_state == "error":
                model_state = "error"
            elif day_model_state == "ok" and model_state != "error":
                model_state = "ok"

        health = float((present_slots / total_slots) * 100.0) if total_slots else 0.0
        total_actual_kwh += actual_kwh
        total_ai_recovered_kwh += recovered_kwh
        total_blended_kwh += blended_kwh
        rows.append({
            "id": nid,
            "name": node["name"],
            "actual_kwh": actual_kwh,
            "recovered_kwh": recovered_kwh,
            "blended_kwh": blended_kwh,
            "health": health,
            "model_state": model_state,
        })

    variance_pct = ((total_blended_kwh - total_actual_kwh) / total_actual_kwh * 100.0) if total_actual_kwh > 0 else 0.0
    variance_color = "text-rose-600" if variance_pct > 0 else "text-emerald-600"
    variance_sign = "+" if variance_pct > 0 else ""

    table_rows = ""
    for r in rows:
        model_badge = (
            "<span class='px-2 py-0.5 rounded bg-emerald-50 text-emerald-600 text-[9px] font-bold'>MODEL</span>"
            if r["model_state"] == "ok"
            else "<span class='px-2 py-0.5 rounded bg-slate-100 text-slate-400 text-[9px] font-bold'>NO MODEL</span>"
            if r["model_state"] == "missing"
            else "<span class='px-2 py-0.5 rounded bg-amber-50 text-amber-600 text-[9px] font-bold'>MODEL ERR</span>"
        )
        health_color = "text-emerald-600" if r["health"] >= 95 else "text-amber-600" if r["health"] > 0 else "text-slate-300"
        table_rows += f"""
        <tr class="hover:bg-slate-50 transition-colors bg-white">
            <td class="p-3 border-b text-[10px] text-slate-500 font-mono">ID:{r['id']}</td>
            <td class="p-3 border-b text-xs font-semibold text-slate-700">{r['name']}</td>
            <td class="p-3 border-b text-right text-xs font-bold text-slate-800">{r['actual_kwh']:,.1f}</td>
            <td class="p-3 border-b text-right text-xs font-bold text-indigo-600">{r['recovered_kwh']:,.1f}</td>
            <td class="p-3 border-b text-right text-xs font-bold text-slate-900">{r['blended_kwh']:,.1f}</td>
            <td class="p-3 border-b text-center text-[10px]"><span class="{health_color} font-mono">{r['health']:.0f}%</span></td>
            <td class="p-3 border-b text-center">{model_badge}</td>
        </tr>
        """

    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8"><title>AIENERGY - End Meter AI / 末端电表AI预测</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            ::-webkit-scrollbar {{ width: 8px; height: 8px; }}
            ::-webkit-scrollbar-track {{ background: #f8fafc; }}
            ::-webkit-scrollbar-thumb {{ background: #cbd5e1; border-radius: 4px; }}
            ::-webkit-scrollbar-thumb:hover {{ background: #94a3b8; }}
            @media print {{ @page {{ size: landscape; margin: 10mm; }} body {{ background: white; }} }}
        </style>
    </head>
    <body class="bg-slate-100 min-h-screen font-sans text-slate-900 pb-12">
        <div class="max-w-[1400px] mx-auto mt-8 px-6 space-y-8">
            <header class="bg-white p-8 rounded-2xl shadow-sm border border-slate-200 flex justify-between items-center">
                <div>
                    <h1 class="text-3xl font-black">End Meter AI Spending / 末端电表AI电量</h1>
                    <p class="text-slate-500 font-medium italic mt-1">{days}-Day Window / {days}天窗口 • Start: <span class="font-mono">20{start_date}</span> • AI Model Comparison / AI模型对比</p>
                </div>
                <div class="text-right">
                    <div class="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Models Loaded / 已加载模型</div>
                    <div class="text-xl font-black text-indigo-600">{loaded_models} / {len(rows)}</div>
                </div>
            </header>

            <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div class="bg-white p-6 rounded-2xl shadow-sm border border-slate-200">
                    <span class="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Actual Total Spending / 实际总用电</span>
                    <div class="text-3xl font-black text-slate-800 mt-1">{total_actual_kwh:,.1f} <span class="text-sm font-light text-slate-400">kWh</span></div>
                </div>
                <div class="bg-slate-900 p-6 rounded-2xl shadow-xl text-white">
                    <span class="text-[10px] font-bold text-indigo-300 uppercase tracking-widest text-opacity-80">AI Recovered Missing / AI补全缺失</span>
                    <div class="text-3xl font-black mt-1">{total_ai_recovered_kwh:,.1f} <span class="text-sm font-light text-slate-400">kWh</span></div>
                </div>
                <div class="bg-white p-6 rounded-2xl shadow-sm border border-slate-200">
                    <span class="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Total Blended / 最终合并总量</span>
                    <div class="text-3xl font-black mt-1 text-slate-900">{total_blended_kwh:,.1f} <span class="text-sm font-light text-slate-400">kWh</span></div>
                    <div class="text-xs mt-1 {variance_color}">vs Actual: {variance_sign}{variance_pct:.1f}%</div>
                </div>
            </div>

            <div class="bg-white p-4 rounded-2xl shadow-sm border border-slate-200">
                <span class="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Blend Rule / 合并规则</span>
                <div class="font-mono text-xs text-slate-600 mt-1">blended = actual.fillna(predicted)</div>
                <div class="font-mono text-xs text-slate-500 mt-1">kWh = sum(Pt_15min) * (15/60)</div>
            </div>

            <section class="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden">
                <div class="bg-slate-900 p-4 text-white flex justify-between items-center">
                    <h3 class="font-bold text-xs uppercase tracking-widest">Actual vs AI by End Meter / 每台设备实际与预测</h3>
                    <span class="text-[10px] text-slate-400 font-mono">Columns: actual, AI-recovered, total blended</span>
                </div>
                <div class="overflow-x-auto">
                    <table class="w-full text-left border-collapse min-w-max">
                        <thead>
                            <tr>
                                <th class="p-3 border-b text-xs font-bold text-slate-500 bg-slate-50 w-16">Node / 节点</th>
                                <th class="p-3 border-b text-xs font-bold text-slate-500 bg-slate-50">Machine Name / 设备名称</th>
                                <th class="p-3 border-b text-xs font-bold text-slate-500 bg-slate-50 text-right">Actual (kWh)</th>
                                <th class="p-3 border-b text-xs font-bold text-slate-500 bg-slate-50 text-right">AI-Recovered (kWh)</th>
                                <th class="p-3 border-b text-xs font-bold text-slate-500 bg-slate-50 text-right">Total Blended (kWh)</th>
                                <th class="p-3 border-b text-xs font-bold text-slate-500 bg-slate-50 text-center">Data Health / 数据完整度</th>
                                <th class="p-3 border-b text-xs font-bold text-slate-500 bg-slate-50 text-center">Model / 模型</th>
                            </tr>
                        </thead>
                        <tbody class="divide-y divide-slate-100">{table_rows}</tbody>
                    </table>
                </div>
            </section>

            <footer class="text-center text-slate-400 text-[10px] py-4">Generated by AIENERGY System / AI能源系统生成 • End Meter AI Report / 末端电表AI报告</footer>
        </div>
    </body>
    </html>
    """

    with open(report_file, "w", encoding="utf-8") as f:
        f.write(html_content)
    try:
        conn.close()
    except Exception:
        pass
    print(f"✅ End Meter AI report generated: {report_file}")
    if open_browser:
        webbrowser.open(Path(report_file).resolve().as_uri())
    return report_file


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=str, default="260301", help="Start date (YYMMDD)")
    parser.add_argument("--days", type=int, default=7, help="Number of days")
    parser.add_argument("--model-dir", type=str, default=None, help="Optional model artifacts directory")
    parser.add_argument("--no-browser", action="store_true", help="Do not open browser after generating")
    args = parser.parse_args()
    main(args.start, days=max(1, int(args.days)), model_dir=args.model_dir, open_browser=not args.no_browser)
