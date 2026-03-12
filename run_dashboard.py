#!/usr/bin/env python3
"""
AI Energy Dashboard - Launch script
Starts the Flask server and opens the dashboard in the default browser.
"""

import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

# Ensure we're in the project root
PROJECT_ROOT = Path(__file__).resolve().parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

def open_browser():
    """Open browser after a short delay to allow server to start."""
    time.sleep(1.2)
    webbrowser.open("http://localhost:5050")

if __name__ == "__main__":
    port = 5050
    url = f"http://localhost:{port}"

    # Start browser in background thread
    threading.Thread(target=open_browser, daemon=True).start()

    print("=" * 50)
    print("  AI Energy Dashboard / 智能能源")
    print("=" * 50)
    print(f"  {url}")
    print("=" * 50)
    print("  Press Ctrl+C to stop")
    print()

    from dashboard import app
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
