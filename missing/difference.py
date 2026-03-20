#!/usr/bin/env python3
"""
Energy balance gap analysis — reconciles the report's (Grid + Solar − Consumption) residual.

Writes HTML to missing/data/ (same visual language as nodes/data/*.html) instead of printing tables to the terminal.

Usage:
  python missing/difference.py --start 260301 --days 7
  python missing/difference.py --start 260301 --days 7 --grid-id 1
"""

from __future__ import annotations

import argparse
import html as html_module
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta

import mysql.connector

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
NODES_DIR = os.path.join(ROOT_DIR, "nodes")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "data")
if NODES_DIR not in sys.path:
    sys.path.insert(0, NODES_DIR)

import report as rep  # noqa: E402

EXCLUDED_IDS = rep.EXCLUDED_IDS
STORAGE_IDS = [30]
PV_EXCLUDED_FROM_CONSUMPTION = [56, 29, 31]


def generate_date_range(start_date_str: str, days: int) -> list[str]:
    start_dt = datetime.strptime(f"20{start_date_str}", "%Y%m%d")
    return [(start_dt + timedelta(days=i)).strftime("%y%m%d") for i in range(days)]


def walk_unmetered_nodes(node: dict, path: list[str] | None = None):
    if path is None:
        path = []
    name = node.get("name") or "?"
    here = path + [name]
    if node.get("id") is None:
        yield " / ".join(here), node.get("category", "")
    for sub in node.get("sub_nodes") or []:
        yield from walk_unmetered_nodes(sub, here)


def meter_label(nid: int, tree: dict) -> str:
    root = tree.get("root_node", {})

    def find(n: dict, tid: int) -> str | None:
        if n.get("id") == tid:
            return n.get("name", str(tid))
        for s in n.get("sub_nodes") or []:
            r = find(s, tid)
            if r:
                return r
        return None

    return find(root, nid) or f"Node {nid}"


def esc(s: str) -> str:
    return html_module.escape(s, quote=True)


def build_html(
    *,
    start_yymmdd: str,
    days: int,
    grid_id: int,
    grid_name: str,
    grid: dict,
    solar: dict,
    consumption: dict,
    end_meter_count: int,
    solar_node_count: int,
    supply: float,
    cons: float,
    headline: float,
    gexp: float,
    supply_net: float,
    gap_net: float,
    storage_kwh: float,
    adjusted_headline: float,
    adjusted_net: float,
    export_pct_of_headline: float,
    excluded_rows: list[tuple[int, str, str, float]],
    unmetered: list[tuple[str, str]],
    by_cat: list[tuple[str, int]],
) -> str:
    pct_cons = (headline / cons * 100) if cons > 0 else 0.0
    end_d = start_yymmdd
    start_iso = f"20{end_d[0:2]}-{end_d[2:4]}-{end_d[4:6]}"
    last_d = (datetime.strptime(f"20{start_yymmdd}", "%Y%m%d") + timedelta(days=days - 1)).strftime(
        "%Y-%m-%d"
    )
    title = "Energy Difference Analysis / 能源差值分析 | AIENERGY"

    excluded_table = ""
    for nid, label, tags, kwh in excluded_rows:
        excluded_table += f"""
        <tr class="hover:bg-slate-50 border-b border-slate-100">
            <td class="p-3 font-mono text-sm font-bold text-slate-700">{nid}</td>
            <td class="p-3 text-xs text-slate-800">{esc(label)}</td>
            <td class="p-3 text-xs text-slate-500">{esc(tags)}</td>
            <td class="p-3 text-right font-mono text-xs font-bold text-slate-800">{kwh:,.2f}</td>
        </tr>"""

    unmetered_rows = ""
    for path, cat in unmetered:
        unmetered_rows += f"""
        <tr class="hover:bg-slate-50 border-b border-slate-100">
            <td class="p-3 text-xs text-slate-500 font-mono">{esc(cat or "?")}</td>
            <td class="p-3 text-xs text-slate-700 leading-snug">{esc(path)}</td>
        </tr>"""

    cat_rows = ""
    for cat, n in by_cat:
        cat_rows += f"""
        <tr class="hover:bg-slate-50 border-b border-slate-100">
            <td class="p-3 text-xs font-medium text-slate-700">{esc(cat)}</td>
            <td class="p-3 text-right font-mono text-xs font-bold text-slate-800">{n}</td>
        </tr>"""

    diff_cls = "text-emerald-300" if headline >= 0 else "text-rose-300"
    net_diff_cls = "text-emerald-300" if gap_net >= 0 else "text-rose-300"

    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>{esc(title)}</title>
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
                <h1 class="text-3xl font-black text-slate-900">Energy Difference Analysis / 能源差值分析</h1>
                <p class="text-slate-500 font-medium italic mt-1">
                    Explains (Grid Import + Solar) − Consumption vs unmetered branches &amp; export / 解释供应与末端用电差值
                    | {days}-Day / {days}天 | {start_iso} → {last_d}
                </p>
                <p class="text-slate-400 text-xs mt-2 font-mono">
                    Grid / 电网: {esc(grid_name)} (ID:{grid_id}) • Solar / 太阳能: {solar_node_count} nodes / 节点
                    • Consumption / 用电: {end_meter_count} end meters / 末端电表 (excl. 56,29,31,30)
                </p>
            </header>

            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4">
                <div class="bg-emerald-900 p-6 rounded-2xl shadow-xl text-white">
                    <span class="text-[10px] font-bold text-emerald-300 uppercase tracking-widest">Grid Import / 电网输入</span>
                    <div class="text-2xl font-black mt-1">{grid["total_import_kwh"]:,.1f} <span class="text-sm font-light">kWh</span></div>
                </div>
                <div class="bg-amber-600 p-6 rounded-2xl shadow-xl text-white">
                    <span class="text-[10px] font-bold text-amber-200 uppercase tracking-widest">Solar / 太阳能</span>
                    <div class="text-2xl font-black mt-1">{solar["total_kwh"]:,.1f} <span class="text-sm font-light">kWh</span></div>
                </div>
                <div class="bg-indigo-900 p-6 rounded-2xl shadow-xl text-white">
                    <span class="text-[10px] font-bold text-indigo-300 uppercase tracking-widest">Consumption / 用电量</span>
                    <div class="text-2xl font-black mt-1">{cons:,.1f} <span class="text-sm font-light">kWh</span></div>
                </div>
                <div class="bg-slate-800 p-6 rounded-2xl shadow-xl text-white">
                    <span class="text-[10px] font-bold text-slate-300 uppercase tracking-widest">Headline gap / 标题差值</span>
                    <div class="text-2xl font-black mt-1 {diff_cls}">{headline:+,.1f} <span class="text-sm font-light">kWh</span></div>
                    <div class="text-xs text-slate-400 mt-1">({pct_cons:+.1f}% of consumption / 占用电量)</div>
                </div>
                <div class="bg-blue-900 p-6 rounded-2xl shadow-xl text-white">
                    <span class="text-[10px] font-bold text-blue-300 uppercase tracking-widest">Grid Export / 电网输出</span>
                    <div class="text-2xl font-black mt-1">{gexp:,.1f} <span class="text-sm font-light">kWh</span></div>
                </div>
            </div>

            <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div class="bg-white p-6 rounded-2xl shadow-sm border border-slate-200">
                    <span class="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Supply net / 净供应</span>
                    <div class="text-2xl font-black mt-1 text-slate-800">Imp + Sol − Exp</div>
                    <div class="text-xl font-mono font-bold text-slate-900 mt-1">{supply_net:,.1f} <span class="text-sm font-normal text-slate-500">kWh</span></div>
                </div>
                <div class="bg-white p-6 rounded-2xl shadow-sm border border-slate-200">
                    <span class="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Gap vs net / 净差值</span>
                    <div class="text-2xl font-black mt-1 {net_diff_cls}">{gap_net:+,.1f} <span class="text-sm font-light text-slate-600">kWh</span></div>
                    <div class="text-xs text-slate-500 mt-1">Supply_net − Consumption</div>
                </div>
                <div class="bg-white p-6 rounded-2xl shadow-sm border border-slate-200">
                    <span class="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Storage id 30 / 储能</span>
                    <div class="text-2xl font-black mt-1 text-violet-700">{storage_kwh:+,.1f} <span class="text-sm font-light text-slate-500">kWh</span></div>
                    <div class="text-xs text-slate-500 mt-1">Signed Σ Pt (same rule as report)</div>
                </div>
            </div>

            <section class="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden">
                <div class="bg-slate-800 p-4 border-b border-slate-700 text-white">
                    <h3 class="font-bold text-xs uppercase tracking-widest">Excluded meters (not in consumption) / 排除表计</h3>
                </div>
                <div class="overflow-x-auto">
                    <table class="w-full text-sm">
                        <thead class="bg-slate-50 text-slate-500 border-b">
                            <tr>
                                <th class="p-3 font-bold text-left">ID</th>
                                <th class="p-3 font-bold text-left">Name / 名称</th>
                                <th class="p-3 font-bold text-left">Note / 说明</th>
                                <th class="p-3 font-bold text-right">Pt integral (kWh) / 积分</th>
                            </tr>
                        </thead>
                        <tbody>{excluded_table}</tbody>
                    </table>
                </div>
            </section>

            <section class="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden">
                <div class="bg-slate-800 p-4 border-b border-slate-700 text-white flex justify-between items-center">
                    <h3 class="font-bold text-xs uppercase tracking-widest">Reconciliation / 对账提示</h3>
                </div>
                <div class="p-6 text-sm text-slate-600 space-y-3">
                    <p>Export explains roughly <span class="font-mono font-bold text-slate-800">{gexp:,.0f} kWh</span>
                    ({export_pct_of_headline:.1f}% of headline gap magnitude) of the headline surplus when treating export as energy leaving the site.</p>
                    <p>After netting export from supply, about <span class="font-mono font-bold text-slate-800">{gap_net:,.0f} kWh</span> remains vs end-meter consumption.</p>
                    <p class="text-xs text-slate-500">Headline − storage (heuristic): <span class="font-mono font-semibold">{adjusted_headline:+,.1f} kWh</span>
                    &nbsp;|&nbsp; Net gap − storage: <span class="font-mono font-semibold">{adjusted_net:+,.1f} kWh</span></p>
                </div>
            </section>

            <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <section class="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden">
                    <div class="bg-slate-800 p-4 border-b border-slate-700 text-white">
                        <h3 class="font-bold text-xs uppercase tracking-widest">Unmetered by category / 无表计分类</h3>
                    </div>
                    <div class="overflow-x-auto">
                        <table class="w-full text-sm">
                            <thead class="bg-slate-50 text-slate-500 border-b">
                                <tr>
                                    <th class="p-3 font-bold text-left">Category / 类别</th>
                                    <th class="p-3 font-bold text-right">Count / 数量</th>
                                </tr>
                            </thead>
                            <tbody>{cat_rows}</tbody>
                        </table>
                    </div>
                </section>
                <section class="bg-white rounded-2xl shadow-sm border border-slate-200 p-6">
                    <h3 class="font-bold text-xs uppercase tracking-widest text-slate-500 mb-3">Notes / 说明</h3>
                    <ul class="text-xs text-slate-600 space-y-1 font-mono">
                        <li>• Aligned with nodes/report.py: same grid, solar, and consumption integration.</li>
                        <li>• PV 56,29,31: in Solar column, not Consumption — do not add again to “close” the gap.</li>
                        <li>• Storage 30: excluded from consumption; large gaps are usually not mostly ESS unless Pt on 30 is large.</li>
                        <li>• Nodes in relation.json without <code class="bg-slate-100 px-1 rounded">id</code> never roll into site_power totals.</li>
                        <li>• column.json documents telemetry keys; kWh here uses ImpEP/ExpEP/Pt only.</li>
                    </ul>
                </section>
            </div>

            <section class="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden">
                <div class="bg-slate-800 p-4 border-b border-slate-700 text-white">
                    <h3 class="font-bold text-xs uppercase tracking-widest">Unmetered topology (no id) / 拓扑无表号 ({len(unmetered)} items)</h3>
                </div>
                <div class="overflow-x-auto max-h-[480px] overflow-y-auto">
                    <table class="w-full text-sm">
                        <thead class="bg-slate-50 text-slate-500 border-b sticky top-0">
                            <tr>
                                <th class="p-3 font-bold text-left w-40">Category / 类别</th>
                                <th class="p-3 font-bold text-left">Path / 路径</th>
                            </tr>
                        </thead>
                        <tbody>{unmetered_rows}</tbody>
                    </table>
                </div>
            </section>

            <section class="bg-white rounded-2xl shadow-sm border border-slate-200 p-6">
                <h3 class="font-bold text-xs uppercase tracking-widest text-slate-500 mb-3">Data quality (reference) / 数据质量</h3>
                <div class="overflow-x-auto">
                    <table class="w-full text-sm">
                        <thead class="text-slate-500 border-b">
                            <tr>
                                <th class="p-2 text-left font-bold">Source / 来源</th>
                                <th class="p-2 text-right font-bold">Median intv / 中位间隔</th>
                                <th class="p-2 text-right font-bold">Reliability / 可靠性</th>
                            </tr>
                        </thead>
                        <tbody class="divide-y divide-slate-100">
                            <tr class="hover:bg-slate-50">
                                <td class="p-2 font-bold text-emerald-700">Grid</td>
                                <td class="p-2 text-right font-mono">{grid["median_interval_min"]} min</td>
                                <td class="p-2 text-right font-bold text-emerald-600">{grid["overall_health"]:.1f}%</td>
                            </tr>
                            <tr class="hover:bg-slate-50">
                                <td class="p-2 font-bold text-amber-600">Solar</td>
                                <td class="p-2 text-right font-mono">{solar["median_interval_min"]} min</td>
                                <td class="p-2 text-right font-bold text-emerald-600">{solar["overall_health"]:.1f}%</td>
                            </tr>
                            <tr class="hover:bg-slate-50">
                                <td class="p-2 font-bold text-indigo-700">Consumption</td>
                                <td class="p-2 text-right font-mono">{consumption["median_interval_min"]} min</td>
                                <td class="p-2 text-right font-bold text-emerald-600">{consumption["overall_health"]:.1f}%</td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </section>

            <footer class="text-center text-slate-400 text-[10px] py-8 border-t border-slate-100">
                Generated by AIENERGY System / AI能源系统生成 • Energy Difference Analysis / 能源差值分析 • {esc(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))}
            </footer>
        </div>
    </body>
    </html>
    """


def main():
    parser = argparse.ArgumentParser(description="Energy gap analysis → HTML report")
    parser.add_argument("--start", type=str, default="260301", help="Start date YYMMDD")
    parser.add_argument("--days", type=int, default=7, help="Number of days")
    parser.add_argument("--grid-id", type=int, default=1, help="Grid odometer meter id")
    args = parser.parse_args()

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_name = f"differance-20{args.start}-{args.days}d.htm"
    out_path = os.path.join(OUTPUT_DIR, out_name)

    dates = generate_date_range(args.start, args.days)
    json_path = os.path.join(ROOT_DIR, "relation.json")
    with open(json_path, "r", encoding="utf-8") as f:
        tree = json.load(f)
    root = tree.get("root_node", {})
    grid_name = rep.get_node_name_from_json(args.grid_id)

    solar_nodes = rep.get_renewable_nodes(root)
    end_meters = rep.get_end_meters(root)
    unmetered = list(walk_unmetered_nodes(root))
    by_cat = Counter(c or "?" for _, c in unmetered).most_common()

    try:
        conn = mysql.connector.connect(**rep.DB_CONFIG)
        cursor = conn.cursor(dictionary=True)
    except Exception as e:
        print(f"DB connection failed: {e}", file=sys.stderr)
        return 1

    grid = rep.fetch_grid_data(cursor, dates, args.grid_id)
    solar = rep.fetch_power_based_data(cursor, dates, solar_nodes)
    consumption = rep.fetch_power_based_data(cursor, dates, end_meters)

    supply = grid["total_import_kwh"] + solar["total_kwh"]
    cons = consumption["total_kwh"]
    headline = supply - cons
    gexp = grid["total_export_kwh"]
    supply_net = supply - gexp
    gap_net = supply_net - cons

    excluded_rows: list[tuple[int, str, str, float]] = []
    for nid in EXCLUDED_IDS:
        label = meter_label(nid, tree)
        tag_parts = []
        if nid in STORAGE_IDS:
            tag_parts.append("storage")
        if nid in PV_EXCLUDED_FROM_CONSUMPTION:
            tag_parts.append("PV → Solar column")
        tags = ", ".join(tag_parts)
        one = rep.fetch_power_based_data(cursor, dates, [{"id": nid, "name": label}])
        excluded_rows.append((nid, label, tags, one["total_kwh"]))

    storage_block = rep.fetch_power_based_data(
        cursor, dates, [{"id": 30, "name": meter_label(30, tree)}]
    )
    storage_kwh = storage_block["total_kwh"]
    adjusted_headline = headline - storage_kwh
    adjusted_net = gap_net - storage_kwh
    export_pct = (100 * gexp / headline) if headline else 0.0

    conn.close()

    html_doc = build_html(
        start_yymmdd=args.start,
        days=args.days,
        grid_id=args.grid_id,
        grid_name=grid_name,
        grid=grid,
        solar=solar,
        consumption=consumption,
        end_meter_count=len(end_meters),
        solar_node_count=len(solar_nodes),
        supply=supply,
        cons=cons,
        headline=headline,
        gexp=gexp,
        supply_net=supply_net,
        gap_net=gap_net,
        storage_kwh=storage_kwh,
        adjusted_headline=adjusted_headline,
        adjusted_net=adjusted_net,
        export_pct_of_headline=export_pct,
        excluded_rows=excluded_rows,
        unmetered=unmetered,
        by_cat=by_cat,
    )

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_doc.strip() + "\n")

    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
