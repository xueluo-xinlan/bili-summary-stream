import sqlite3
import time
from pathlib import Path
from typing import List, Dict, Any

class Storage:
    def __init__(self, db_path: str = "data/history.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path))

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS processed_videos (
                bvid TEXT PRIMARY KEY,
                title TEXT,
                owner_name TEXT,
                source_type TEXT,
                processed_at INTEGER,
                status TEXT
            )
            """)
            # 幂等迁移：note 列记录"为何待补推"，便于排查（旧库自动补列）
            try:
                conn.execute("ALTER TABLE processed_videos ADD COLUMN note TEXT")
            except sqlite3.OperationalError:
                pass
            conn.commit()

    def is_processed(self, bvid: str) -> bool:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM processed_videos WHERE bvid = ?", (bvid,))
            return cursor.fetchone() is not None

    def get_pending(self, limit: int = 50) -> List[Dict[str, Any]]:
        """取待补推记录（status 非 success）——按时间正序，先来先补。"""
        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT * FROM processed_videos "
                "WHERE status IS NULL OR status != 'success' "
                "ORDER BY processed_at ASC LIMIT ?", (limit,))
            return [dict(r) for r in cur.fetchall()]

    def mark_pending(self, bvid: str, title: str = "", owner_name: str = "",
                     source_type: str = "manual", note: str = ""):
        """记入待补推。幂等：同一 bvid 反复标记只保留一行。"""
        with self._get_conn() as conn:
            conn.execute("""
            INSERT OR REPLACE INTO processed_videos
                (bvid, title, owner_name, source_type, processed_at, status, note)
            VALUES (?, ?, ?, ?, ?, 'pending', ?)
            """, (bvid, title, owner_name, source_type, int(time.time()), note[:200]))
            conn.commit()

    def mark_processed(
        self,
        bvid: str,
        title: str = "",
        owner_name: str = "",
        source_type: str = "manual",
        status: str = "success",
        note: str = ""
    ):
        with self._get_conn() as conn:
            conn.execute("""
            INSERT OR REPLACE INTO processed_videos (bvid, title, owner_name, source_type, processed_at, status, note)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (bvid, title, owner_name, source_type, int(time.time()), status, note[:200]))
            conn.commit()

    def get_recent_processed(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM processed_videos ORDER BY processed_at DESC LIMIT ?", (limit,))
            return [dict(row) for row in cursor.fetchall()]
