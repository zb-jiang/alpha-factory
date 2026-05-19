"""
Step 00: 清理临时输出文件

在开始新的因子挖掘流程前，清理 outputs 目录中的临时内容，
确保从一个干净的状态开始。

清理内容包括:
- health/ 目录下的特征体检文件
- iter_*/ 目录下的迭代中间文件
- train_windows/ 目录下的窗口归档文件
- backtest/ 目录下的回测结果文件
- llm/ 目录下的大模型输出文件
- _runtime/active_context.json 运行态上下文文件

保留内容:
- config/ 目录下的配置文件（由用户维护）
- data/ 目录下的数据源文件（下载成本高）
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from common import log_step_end, log_step_start


def get_project_root() -> Path:
    """获取项目根目录"""
    # 脚本位于 runtime/src/ 目录下，项目根目录是 runtime
    return Path(__file__).resolve().parent.parent


def clean_outputs(dry_run: bool = False) -> dict[str, int]:
    """
    清理 outputs 目录中的临时文件
    
    Args:
        dry_run: 如果为 True，只打印将要删除的内容，不实际删除
        
    Returns:
        统计信息字典，包含删除的文件和目录数量
    """
    project_root = get_project_root()
    outputs_dir = project_root / "outputs"
    
    if not outputs_dir.exists():
        print(f"outputs 目录不存在，创建空目录结构")
        outputs_dir.mkdir(parents=True, exist_ok=True)
        # 创建必要的子目录
        (outputs_dir / "health").mkdir(exist_ok=True)
        (outputs_dir / "llm").mkdir(exist_ok=True)
        (outputs_dir / "backtest").mkdir(exist_ok=True)
        return {"files": 0, "directories": 3}
    
    stats = {"files": 0, "directories": 0}
    
    # 定义要清理的目录和文件模式
    items_to_clean = [
        # 健康检查目录
        ("health/*", "files"),
        # 迭代目录
        ("iter_*", "directories"),
        # 训练窗口归档
        ("train_windows", "directories"),
        # 回测结果
        ("backtest/*", "files"),
        # LLM 输出
        ("llm/*", "files"),
        # 多 Agent 模式下分析师中间产物
        ("llm/agent_outputs", "directories"),
        # 当前运行态上下文
        ("_runtime/active_context.json", "files"),
    ]
    
    print(f"{'[预览模式] ' if dry_run else ''}开始清理 outputs 目录...")
    print(f"目标目录: {outputs_dir.resolve()}")
    print("-" * 50)
    
    for pattern, item_type in items_to_clean:
        full_pattern = outputs_dir / pattern
        
        if item_type == "directories":
            # 匹配目录
            for item in outputs_dir.glob(pattern.replace("/*", "")):
                if item.is_dir():
                    if dry_run:
                        print(f"[将删除目录] {item.relative_to(project_root)}")
                    else:
                        print(f"[删除目录] {item.relative_to(project_root)}")
                        shutil.rmtree(item)
                    stats["directories"] += 1
        else:
            # 匹配文件
            parent_dir = full_pattern.parent
            file_pattern = full_pattern.name
            
            if parent_dir.exists():
                for item in parent_dir.glob(file_pattern):
                    if item.is_file():
                        if dry_run:
                            print(f"[将删除文件] {item.relative_to(project_root)}")
                            stats["files"] += 1
                        else:
                            try:
                                print(f"[删除文件] {item.relative_to(project_root)}")
                                item.unlink()
                                stats["files"] += 1
                            except PermissionError:
                                print(f"  [跳过] 文件被占用，无法删除: {item.name}")
                            except Exception as e:
                                print(f"  [跳过] 删除失败: {item.name}, 错误: {e}")
    
    print("-" * 50)
    if dry_run:
        print(f"预览完成。将删除 {stats['files']} 个文件，{stats['directories']} 个目录")
    else:
        print(f"清理完成。已删除 {stats['files']} 个文件，{stats['directories']} 个目录")
    
    return stats


def run() -> None:
    dry_run = "--dry-run" in sys.argv or "-n" in sys.argv

    log_step_start("00", "清理输出目录")

    if dry_run:
        print("  干运行模式（预览模式），不会实际删除任何文件")

    stats = clean_outputs(dry_run=dry_run)

    if not dry_run and stats["files"] == 0 and stats["directories"] == 0:
        print("  outputs 目录已经是干净的，无需清理")

    log_step_end("00", "清理完成", details=[f"删除 {stats['files']} 个文件, {stats['directories']} 个目录"])


if __name__ == "__main__":
    run()
