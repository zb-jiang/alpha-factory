from __future__ import annotations

from common import archive_iteration_outputs, ensure_runtime_dirs, env_config, write_json
from step01_init_qlib import run as run_step01
from step02_build_feature_pool import run as run_step02
from step03_health_check import run as run_step03
from step04_build_summary import run as run_step04
from step05_call_llm import run as run_step05
from step06_validate_factor import run as run_step06
from step07_eval_factor import run as run_step07
from step08_backtest import run as run_step08
from step09_score import run as run_step09


def run() -> None:
    ensure_runtime_dirs()
    config = env_config()
    iterations = int(config.get("iteration_count", 3))
    for iteration in range(1, iterations + 1):
        run_step01()
        run_step02()
        run_step03()
        run_step04()
        run_step05()
        run_step06()
        run_step07()
        run_step08()
        run_step09()
        write_json(
            OUTPUT_DIR / "backtest" / "iteration_context.json",
            {"iteration": iteration, "status": "completed"},
        )
        archive_iteration_outputs(iteration)
        print(f"iteration {iteration} ok")


if __name__ == "__main__":
    from common import OUTPUT_DIR

    run()
