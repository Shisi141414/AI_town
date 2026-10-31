"""独立于世界存档的机制编辑记录；记录可打开，但不会自动应用。"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path


_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{12}$")


class MechanicsRecords:
    def __init__(self, folder: Path):
        self.folder = Path(folder)

    def _path(self, record_id: str) -> Path:
        if not _ID.fullmatch(record_id):
            raise ValueError("记录编号无效")
        return self.folder / f"{record_id}.json"

    def create(self, payload: dict, status: str = "draft", reason: str = "",
               raw_fields: dict | None = None) -> dict:
        if status not in {"draft", "applied", "failed"}:
            raise ValueError("记录状态无效")
        now = datetime.now().astimezone()
        record_id = now.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:12]
        label = now.strftime("%Y-%m-%d %H:%M:%S")
        if status == "failed":
            label += " · 未成功：" + (reason.strip() or "未知原因")[:100]
        record = {"id": record_id, "name": label, "created": now.isoformat(timespec="seconds"),
                  "status": status, "reason": reason, "payload": payload,
                  "raw_fields": raw_fields or {}}
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self._path(record_id)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(record, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        temp.replace(path)
        return self.metadata(record)

    @staticmethod
    def metadata(record: dict) -> dict:
        return {key: record[key] for key in ("id", "name", "created", "status", "reason")}

    def read(self, record_id: str) -> dict:
        path = self._path(record_id)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise FileNotFoundError("机制记录不存在")

    def listing(self) -> list[dict]:
        if not self.folder.exists():
            return []
        rows = []
        for path in self.folder.glob("*.json"):
            try:
                rows.append(self.metadata(self.read(path.stem)))
            except (ValueError, KeyError, json.JSONDecodeError, FileNotFoundError):
                continue
        return sorted(rows, key=lambda row: row["id"], reverse=True)

    def rename(self, record_id: str, name: str) -> dict:
        name = name.strip()
        if not name or len(name) > 160:
            raise ValueError("名称须为 1 至 160 个字符")
        record = self.read(record_id)
        record["name"] = name
        path = self._path(record_id)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(record, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        temp.replace(path)
        return self.metadata(record)

    def delete_many(self, record_ids: list[str]) -> int:
        paths = [self._path(record_id) for record_id in dict.fromkeys(record_ids)]
        if not paths or any(not path.is_file() for path in paths):
            raise FileNotFoundError("有记录已不存在，请刷新后重试")
        for path in paths:
            path.unlink()
        return len(paths)
