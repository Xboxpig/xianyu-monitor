#!/usr/bin/env python3
"""批量重生成残缺的 AI 分析标准（criteria）文件。

扫描 SQLite 中所有 AI 判断模式的任务，若其 criteria 文件缺失或未通过
validate_generated_criteria 完整性校验，则用任务保存的「详细需求」重新生成。

用法:
  python scripts/regenerate_criteria.py                      # 处理所有残缺任务
  python scripts/regenerate_criteria.py --task-id 0 --task-id 5  # 只处理指定任务
  python scripts/regenerate_criteria.py --db path/to/app.sqlite3
"""
import argparse
import asyncio
import os
import sqlite3
import sys
from pathlib import Path

# 允许以 `python scripts/regenerate_criteria.py` 方式从任意目录运行
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.prompt_utils import generate_criteria, validate_generated_criteria
from src.services.task_generation_runner import build_criteria_filename


def load_tasks(db_path: str) -> list:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, task_name, keyword, description, decision_mode, "
        "ai_prompt_criteria_file FROM tasks ORDER BY id"
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


async def regenerate_one(task: dict) -> str:
    criteria_file = task["ai_prompt_criteria_file"] or build_criteria_filename(
        task["keyword"]
    )
    description = (task["description"] or "").strip()
    if not description:
        print(f"[{task['id']}] 跳过: 任务没有详细需求描述")
        return "skipped"

    if os.path.exists(criteria_file):
        try:
            with open(criteria_file, encoding="utf-8") as f:
                existing = f.read()
        except OSError as exc:
            print(f"[{task['id']}] 读取现有文件失败: {exc}")
        else:
            if not validate_generated_criteria(existing):
                print(f"[{task['id']}] ✅ 现有标准完整，跳过 ({len(existing)} 字符)")
                return "ok"

    print(
        f"[{task['id']}] 重新生成标准: 任务={task['task_name']!r} "
        f"需求={description[:40]!r} -> {criteria_file}"
    )
    try:
        generated = await generate_criteria(
            user_description=description,
            reference_file_path="prompts/macbook_criteria.txt",
        )
    except Exception as exc:
        print(f"[{task['id']}] ❌ 生成失败: {exc}")
        return "failed"

    problems = validate_generated_criteria(generated)
    if problems:
        print(f"[{task['id']}] ❌ 生成结果仍不完整: {'; '.join(problems)}")
        return "failed"

    os.makedirs(os.path.dirname(criteria_file) or ".", exist_ok=True)
    with open(criteria_file, "w", encoding="utf-8") as f:
        f.write(generated)
    print(f"[{task['id']}] ✅ 已保存 ({len(generated)} 字符)")
    return "regenerated"


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/app.sqlite3")
    parser.add_argument(
        "--task-id", type=int, action="append", help="只处理指定任务 ID，可多次指定"
    )
    args = parser.parse_args()

    tasks = load_tasks(args.db)
    ai_tasks = [
        t for t in tasks if str(t["decision_mode"] or "").strip().lower() == "ai"
    ]
    if args.task_id:
        wanted = set(args.task_id)
        ai_tasks = [t for t in ai_tasks if t["id"] in wanted]
        missing = wanted - {t["id"] for t in ai_tasks}
        if missing:
            print(f"⚠️ 指定任务 ID 不在 AI 任务列表或不存在: {sorted(missing)}")

    print(f"待检查 AI 任务: {len(ai_tasks)} 个")
    results = {"ok": 0, "regenerated": 0, "skipped": 0, "failed": 0}
    for task in ai_tasks:
        status = await regenerate_one(task)
        results[status] = results.get(status, 0) + 1
    print(f"完成: 完整跳过 {results['ok']}, 重生成 {results['regenerated']}, "
          f"跳过(无需求) {results['skipped']}, 失败 {results['failed']}")


if __name__ == "__main__":
    asyncio.run(main())
