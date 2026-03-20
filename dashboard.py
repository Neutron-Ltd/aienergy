"""
AI Energy Dashboard - Flask app and entry point
Run: python dashboard.py
Serves the dashboard UI and API for running analyses and viewing report history.
"""

import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_from_directory

# Project paths
PROJECT_ROOT = Path(__file__).resolve().parent
NODES_DIR = PROJECT_ROOT / "nodes"
DATA_DIR = NODES_DIR / "data"

# Report type config: (script_path, filename_pattern, label)
REPORT_TYPES = {
    "grid": {
        "script": "nodes/griddata.py",
        "pattern": re.compile(r"^griddata-(\d{6})-(\d+)d\.html$"),
        "label_en": "Grid",
        "label_zh": "电网",
    },
    "solar": {
        "script": "nodes/solardata.py",
        "pattern": re.compile(r"^solardata-(\d{6})-(\d+)d\.html$"),
        "label_en": "Solar",
        "label_zh": "太阳能",
    },
    "meters": {
        "script": "nodes/endmetersdata.py",
        "pattern": re.compile(r"^endmeterdata-(\d{6})-(\d+)d\.html$"),
        "label_en": "Meters",
        "label_zh": "电表",
    },
    "report": {
        "script": "nodes/report.py",
        "pattern": re.compile(r"^report-(\d{6})-(\d+)d\.html$"),
        "label_en": "Report",
        "label_zh": "报告",
    },
}

app = Flask(__name__, template_folder=str(PROJECT_ROOT / "templates"))


def get_history(report_type: str):
    """Scan nodes/data for report files matching the type. Return list of {label, path, start, days}."""
    if report_type not in REPORT_TYPES:
        return []
    config = REPORT_TYPES[report_type]
    pattern = config["pattern"]
    results = []
    if not DATA_DIR.exists():
        return results
    for f in DATA_DIR.iterdir():
        if not f.is_file() or not f.suffix == ".html":
            continue
        m = pattern.match(f.name)
        if m:
            start, days = m.group(1), m.group(2)
            label = f"{config['label_en']} {start} {days}d"
            results.append({
                "label": label,
                "path": f.name,
                "start": start,
                "days": int(days),
            })
    # Sort by start date descending (newest first)
    results.sort(key=lambda x: (x["start"], -x["days"]), reverse=True)
    return results


@app.route("/")
def index():
    """Serve the dashboard SPA."""
    return render_template("dashboard.html")


@app.route("/api/history")
def api_history():
    """Return history of generated reports for the given type."""
    report_type = request.args.get("type", "grid")
    if report_type not in REPORT_TYPES:
        return jsonify({"error": "Invalid type", "history": []}), 400
    history = get_history(report_type)
    return jsonify({"history": history})


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    """Run the analysis script for the given type, start date, and days."""
    data = request.get_json() or {}
    report_type = data.get("type", "grid")
    start = data.get("start", "260301")
    days = data.get("days", 7)

    if report_type not in REPORT_TYPES:
        return jsonify({"success": False, "error": "Invalid report type"}), 400

    config = REPORT_TYPES[report_type]
    script_path = PROJECT_ROOT / config["script"]

    if not script_path.exists():
        return jsonify({"success": False, "error": f"Script not found: {script_path}"}), 500

    # Build command: python nodes/griddata.py --start 260201 --days 7 --no-browser
    cmd = [
        sys.executable,
        str(script_path),
        "--start", str(start),
        "--days", str(days),
        "--no-browser",
    ]
    if report_type == "report":
        cmd.extend(["--grid-id", "1"])

    try:
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            return jsonify({
                "success": False,
                "error": result.stderr or result.stdout or "Unknown error",
            }), 500

        # Output filenames match each script's convention
        filenames = {
            "grid": f"griddata-{start}-{days}d.html",
            "solar": f"solardata-{start}-{days}d.html",
            "meters": f"endmeterdata-{start}-{days}d.html",
            "report": f"report-{start}-{days}d.html",
        }
        filename = filenames[report_type]
        report_path = DATA_DIR / filename
        if not report_path.exists():
            return jsonify({
                "success": False,
                "error": f"Report was not generated: {filename}",
            }), 500

        return jsonify({
            "success": True,
            "path": filename,
        })
    except subprocess.TimeoutExpired:
        return jsonify({"success": False, "error": "Analysis timed out"}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/reports/<path:filename>")
def serve_report(filename):
    """Serve generated report HTML files from nodes/data."""
    # Security: ensure filename doesn't escape data dir
    if ".." in filename or filename.startswith("/"):
        return "Forbidden", 403
    return send_from_directory(DATA_DIR, filename)


def _open_browser_delayed():
    """Open browser after a short delay so the server is listening."""
    time.sleep(1.2)
    webbrowser.open("http://localhost:5050")


if __name__ == "__main__":
    os.chdir(PROJECT_ROOT)
    sys.path.insert(0, str(PROJECT_ROOT))

    port = 5050
    url = f"http://localhost:{port}"
    threading.Thread(target=_open_browser_delayed, daemon=True).start()

    print("=" * 50)
    print("  AI Energy Dashboard / 智能能源")
    print("=" * 50)
    print(f"  {url}")
    print("=" * 50)
    print("  Press Ctrl+C to stop")
    print()

    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
