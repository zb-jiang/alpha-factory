from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st


RUNTIME_DIR = Path(__file__).resolve().parents[1]
ALPHALENS_DIR = RUNTIME_DIR / "outputs" / "alphalens"


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return pd.read_json(path, typ="series").to_dict()


def _factor_dirs() -> list[Path]:
    if not ALPHALENS_DIR.exists():
        return []
    return sorted([p for p in ALPHALENS_DIR.iterdir() if p.is_dir()])


def _prepare_plot_frame(df: pd.DataFrame, x_col: str) -> pd.DataFrame:
    if df.empty or x_col not in df.columns:
        return pd.DataFrame()
    plot_df = df.copy()
    y_cols = [c for c in plot_df.columns if c != x_col]
    for col in y_cols:
        plot_df[col] = pd.to_numeric(plot_df[col], errors="coerce")
    valid_y_cols = [c for c in y_cols if plot_df[c].notna().any()]
    if not valid_y_cols:
        return pd.DataFrame()
    plot_df = plot_df[[x_col] + valid_y_cols].dropna(subset=[x_col])
    plot_df = plot_df.dropna(subset=valid_y_cols, how="all")
    plot_df = plot_df.set_index(x_col)
    return plot_df


def _rename_metric_columns(df: pd.DataFrame, prefix: str) -> pd.DataFrame:
    if df.empty:
        return df
    renamed = df.copy()
    mapping: dict[str, str] = {}
    for col in renamed.columns:
        col_str = str(col)
        if col_str.endswith("D"):
            mapping[col] = f"{prefix}@{col_str}"
    if mapping:
        renamed = renamed.rename(columns=mapping)
    return renamed


def _render_factor_detail(factor_dir: Path) -> None:
    st.subheader(f"因子明细: {factor_dir.name}")
    st.info(
        "名词说明: Qmax-Qmin = 平均return@5D(Q5) - 平均return@5D(Q1)；"
        "平均return@5D 表示未来5个交易日收益率的均值，RankIC@5D 表示对应5日窗口的秩相关信息系数，Rank IR 为 RankIC 均值/标准差。"
    )
    detail_summary = _read_json(factor_dir / "summary.json")
    if detail_summary:
        cols = st.columns(5)
        cols[0].metric("样本数", detail_summary.get("sample_count", 0))
        cols[1].metric("观测点", detail_summary.get("observation_count", 0))
        cols[2].metric("Rank IC", round(float(detail_summary.get("rank_ic", detail_summary.get("mean_ic", 0.0))), 6))
        cols[3].metric("Rank IR", round(float(detail_summary.get("rank_ic_ir", 0.0)), 6))
        cols[4].metric("Qmax-Qmin", round(float(detail_summary.get("mean_return_spread_qmax_qmin", 0.0)), 6))

    ic_series = _rename_metric_columns(_read_csv(factor_dir / "ic_series.csv"), "RankIC")
    mean_q = _rename_metric_columns(_read_csv(factor_dir / "mean_return_by_quantile.csv"), "平均return")
    mean_q_group = _rename_metric_columns(_read_csv(factor_dir / "mean_return_by_quantile_by_group.csv"), "平均return")
    ic_group = _rename_metric_columns(_read_csv(factor_dir / "mean_ic_by_group.csv"), "RankIC")

    tab1, tab2, tab3, tab4 = st.tabs(["RankIC时序", "分层收益", "行业分层收益", "行业RankIC"])

    with tab1:
        if ic_series.empty:
            st.info("未找到 ic_series.csv")
        else:
            date_col = "date" if "date" in ic_series.columns else ic_series.columns[0]
            ic_plot = _prepare_plot_frame(ic_series, date_col)
            if not ic_plot.empty:
                ic_plot.index = pd.to_datetime(ic_plot.index, errors="coerce")
                ic_plot = ic_plot[ic_plot.index.notna()]
                if not ic_plot.empty:
                    st.line_chart(ic_plot)
                    ic_cols = [c for c in ic_plot.columns if str(c).startswith("RankIC@")]
                    if ic_cols:
                        ic_col = ic_cols[0]
                        smooth_window = st.slider("RankIC平滑窗口(观测点)", 1, 8, 3, 1)
                        smooth_df = pd.DataFrame({f"{ic_col}_MA": ic_plot[ic_col].rolling(smooth_window, min_periods=1).mean()})
                        st.caption("RankIC平滑曲线")
                        st.line_chart(smooth_df)
                    st.caption(f"有效观测点: {len(ic_plot)}")
                    ic_table = ic_plot.reset_index().rename(columns={ic_plot.index.name: "date"})
                    ic_table["date"] = ic_table["date"].dt.strftime("%Y-%m-%d")
                    st.dataframe(ic_table, use_container_width=True)
                else:
                    st.info("RankIC 时序无有效观测点")
            else:
                st.info("RankIC 时序无可绘制的数值列")

    with tab2:
        if mean_q.empty:
            st.info("未找到 mean_return_by_quantile.csv")
        else:
            x_col = "factor_quantile" if "factor_quantile" in mean_q.columns else mean_q.columns[0]
            mean_q_plot = _prepare_plot_frame(mean_q, x_col)
            if not mean_q_plot.empty:
                st.bar_chart(mean_q_plot)
            else:
                st.info("分层收益无可绘制的数值列")
            st.dataframe(mean_q, use_container_width=True)

    with tab3:
        if mean_q_group.empty:
            st.info("未找到 mean_return_by_quantile_by_group.csv")
        else:
            q_col = "factor_quantile" if "factor_quantile" in mean_q_group.columns else mean_q_group.columns[0]
            g_col = "group" if "group" in mean_q_group.columns else mean_q_group.columns[1]
            r_cols = [c for c in mean_q_group.columns if str(c).startswith("平均return@")]
            if not r_cols:
                st.info("行业分层收益无可绘制列")
            else:
                r_col = r_cols[0]
                chart_df = mean_q_group[[q_col, g_col, r_col]].copy()
                chart_df[r_col] = pd.to_numeric(chart_df[r_col], errors="coerce")
                chart_df = chart_df.dropna(subset=[r_col])
                if chart_df.empty:
                    st.info("行业分层收益为空")
                else:
                    quantiles = sorted(chart_df[q_col].dropna().unique().tolist())
                    selected_q = st.selectbox("选择分位", quantiles, index=len(quantiles) - 1)
                    selected_df = chart_df[chart_df[q_col] == selected_q].copy()
                    selected_df = selected_df.sort_values(r_col, ascending=False)
                    st.caption(f"分位 Q{selected_q} 行业收益（{r_col}）")
                    st.bar_chart(selected_df.set_index(g_col)[r_col])
                    st.dataframe(selected_df, use_container_width=True)

    with tab4:
        if ic_group.empty:
            st.info("未找到 mean_ic_by_group.csv")
        else:
            x_col = "group" if "group" in ic_group.columns else ic_group.columns[0]
            ic_group_plot = _prepare_plot_frame(ic_group, x_col)
            if not ic_group_plot.empty:
                st.bar_chart(ic_group_plot)
            else:
                st.info("行业 RankIC 无可绘制的数值列")
            st.dataframe(ic_group, use_container_width=True)


def main() -> None:
    st.set_page_config(page_title="Step13 Alphalens Dashboard", layout="wide")
    st.title("Step13 Alphalens 可视化看板")
    st.caption(f"数据目录: {ALPHALENS_DIR}")

    if not ALPHALENS_DIR.exists():
        st.error("未找到 outputs/alphalens 目录，请先运行 step12。")
        return

    dirs = _factor_dirs()
    if not dirs:
        st.warning("未发现因子明细目录。")
        return

    factor_names = [p.name for p in dirs]
    selected = st.selectbox("选择因子", factor_names, index=0)
    selected_dir = ALPHALENS_DIR / selected
    _render_factor_detail(selected_dir)


if __name__ == "__main__":
    main()
