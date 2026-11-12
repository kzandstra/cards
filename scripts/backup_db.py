"""Copy the database to backups/pokemon_collection-YYYY-MM-DD-HHMM.db using SQLite's online backup API."""
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.db import ROOT, db_path  # noqa: E402

dest_dir = ROOT / "backups"
dest_dir.mkdir(exist_ok=True)
dest = dest_dir / f"pokemon_collection-{datetime.now():%Y-%m-%d-%H%M}.db"
src = sqlite3.connect(db_path()); dst = sqlite3.connect(dest)
src.backup(dst); dst.close(); src.close()
print(f"backup written: {dest}")
