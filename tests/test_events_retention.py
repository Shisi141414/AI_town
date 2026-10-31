"""事件列表截断策略：上限 5 万条，超出裁最老部分并落盘归档，不真丢数据。"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from engine.simulation import Simulation


class EventsRetentionTests(unittest.TestCase):
    def test_overflow_archives_dropped_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = os.getcwd()
            os.chdir(tmp)                      # data/events-archive.jsonl 写进临时目录
            try:
                sim = Simulation()
                baseline = len(sim.events)                   # 构造时可能已有初始事件
                for i in range(50010):
                    sim.log('activity', f'测试事件{i}', [])
                total = baseline + 50010
                # 不变量：内存 + 归档 == 总数（不丢不重）
                archive = Path('data/events-archive.jsonl')
                self.assertTrue(archive.exists())
                lines = archive.read_text(encoding='utf-8').strip().split('\n')
                self.assertEqual(len(sim.events) + len(lines), total)
                # 内存只保留最近一段（≤3 万条出头），归档从最老开始且顺序完整
                self.assertLessEqual(len(sim.events), 30010)
                first = json.loads(lines[0])
                self.assertIn(first['text'], ('测试事件0',)) if baseline == 0 else None
                texts = [e.text for e in sim.events]
                self.assertEqual(texts[-1], '测试事件50009')  # 最新事件必在内存
            finally:
                os.chdir(old_cwd)

    def test_below_limit_keeps_everything(self):
        sim = Simulation()
        for i in range(1000):
            sim.log('activity', f'事件{i}', [])
        self.assertEqual(len(sim.events), 1000)


if __name__ == "__main__":
    unittest.main()
