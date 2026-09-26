"""
app.py — Streamlit entrypoint. Page routing/tabs only, no business logic:
all analytics live in analytics/*, all LLM calls live in insight/*, all
export logic lives in export/*. This file just wires them together and
manages session state.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone

import streamlit as st

import config
from analytics.anomalies import compute_risk_signals
from analytics.drivers import compute_dimension_drivers, rank_dimensions_by_explanatory_power
from analytics.kpis import compute_all_kpis, select_primary_and_secondary_metrics
from analytics.profiler import (
    PROFILE_PROJECT,
    PROFILE_SNAPSHOT,
    PROFILE_TIMESERIES,
    DatasetProfile,
    profile_dataset,
)
from analytics.snapshot import compute_project_analytics, compute_snapshot_analytics
from analytics.trend import build_trend_series, period_label, split_current_prior
from export.pptx_builder import DeckInputs, KPITileData, ViewExportData, build_deck
from ingestion.classifier import ROLE_DATE, ROLE_DIMENSION, ROLE_METRIC
from insight.budget import get_status
from insight.facts_builder import build_facts_packet
from ui.chart_builder import render_chart_explorer
from ui.components import format_number, format_pct_change, mask_secret
from ui.drivers_view import render_drivers_view
from ui.executive_view import render_executive_view
from ui.project_view import (
    render_project_at_risk_view,
    render_project_gantt_view,
    render_project_overview_view,
    render_project_team_view,
)
from ui.revenue_view import render_primary_metric_view
from ui.risk_view import render_forecast_stub, render_risk_view
from ui.snapshot_view import (
    render_snapshot_breakdown_view,
    render_snapshot_composition_view,
    render_snapshot_executive_view,
    render_snapshot_outliers_view,
)
from ui.upload_page import render_upload_page

st.set_page_config(page_title=config.APP_TITLE, layout="wide")


def _columns_by_role(classifications: dict, role: str) -> list[str]:
    return [c.name for c in classifications.values() if c.role == role]


def run_pipeline() -> dict:
    """Runs ingestion-classified data through analytics once per session
    state change, caching the result in st.session_state so every tab reads
    consistent numbers and export reuses exactly what was shown on screen."""
    df = st.session_state["dataset"]
    classifications = st.session_state["classifications"]

    profile = profile_dataset(df, classifications)

    date_cols = _columns_by_role(classifications, ROLE_DATE)
    metric_cols = _columns_by_role(classifications, ROLE_METRIC)
    dimension_cols = _columns_by_role(classifications, ROLE_DIMENSION)

    snapshot_res = None
    project_res = None

    if profile.profile_type == PROFILE_SNAPSHOT:
        snapshot_res = compute_snapshot_analytics(
            df=df,
            entity_col=profile.entity_dimension,
            primary_metric=profile.primary_metric,
            secondary_metrics=profile.secondary_metrics,
            grouping_cols=profile.grouping_dimensions,
        )
        kpis = snapshot_res.kpis
        primary_metric = profile.primary_metric
        trend = None
        all_dimension_drivers = []
        top_dimension_driver = None
        risk_signals = [
            {"description": f"{o['item']}: {o['issue']} ({o['detail']})", "severity": o.get("severity", "medium")}
            for o in snapshot_res.outliers
        ]
        label = "snapshot across entities"
        facts_packets = {
            primary_metric: {
                "dataset_type": "snapshot",
                "total_items": snapshot_res.total_items,
                "entity_dimension": profile.entity_dimension,
                "primary_metric": profile.primary_metric,
                "pareto_text": snapshot_res.pareto_text,
            }
        } if primary_metric else {}

    elif profile.profile_type == PROFILE_PROJECT:
        assignee_col = next(
            (d for d in profile.grouping_dimensions if re.search(r"(assign|owner|person|member|resource)", d, re.I)),
            (profile.grouping_dimensions[0] if profile.grouping_dimensions else None),
        )
        project_res = compute_project_analytics(
            df=df,
            task_col=profile.entity_dimension,
            project_col=profile.grouping_dimensions[0] if profile.grouping_dimensions else None,
            assignee_col=assignee_col,
            progress_col=profile.rate_metrics[0] if profile.rate_metrics else None,
            duration_col=profile.duration_metric,
            start_date_col=profile.primary_date_col,
            end_date_col=profile.end_date_col,
        )
        kpis = project_res.kpis
        primary_metric = "Completion Rate"
        trend = None
        all_dimension_drivers = []
        top_dimension_driver = None
        risk_signals = [
            {"description": f"{t['task']} ({t.get('project', 'N/A')}): {t['reason']}", "severity": t.get("severity", "high")}
            for t in project_res.at_risk_tasks
        ]
        label = "portfolio tasks"
        facts_packets = {
            "Completion Rate": {
                "dataset_type": "project",
                "total_tasks": project_res.total_tasks,
                "completed_tasks": project_res.completed_tasks,
                "completion_rate_pct": f"{project_res.completion_rate_pct:.1f}%",
            }
        }

    else:
        date_col = profile.primary_date_col
        primary_metric, _ = select_primary_and_secondary_metrics(df, metric_cols)

        trend = None
        df_current, df_prior = df, None
        label = "current vs prior period"

        if date_col and primary_metric:
            trend = build_trend_series(df, date_col, primary_metric)
            if trend is not None:
                df_current, df_prior = split_current_prior(df, date_col, trend)
                label = period_label(trend.granularity)

        kpis = compute_all_kpis(df_current, df_prior, metric_cols)
        if not primary_metric and kpis:
            primary_metric = next(iter(kpis), None)

        all_dimension_drivers = []
        top_dimension_driver = None
        if primary_metric and dimension_cols:
            all_dimension_drivers = rank_dimensions_by_explanatory_power(
                df_current, df_prior, dimension_cols, primary_metric
            )
            top_dimension_driver = all_dimension_drivers[0] if all_dimension_drivers else None

        risk_signals = compute_risk_signals(df_current, df_prior, metric_cols, dimension_cols)

        facts_packets = {}
        for name, kpi in kpis.items():
            dim_driver = top_dimension_driver if name == primary_metric else None
            facts_packets[name] = build_facts_packet(
                kpi, dim_driver, risk_signals if name == primary_metric else None, label
            )

    return {
        "df": df,
        "profile": profile,
        "snapshot_res": snapshot_res,
        "project_res": project_res,
        "date_col": profile.primary_date_col,
        "metric_cols": metric_cols,
        "dimension_cols": dimension_cols,
        "trend": trend,
        "kpis": kpis,
        "primary_metric": primary_metric,
        "all_dimension_drivers": all_dimension_drivers,
        "top_dimension_driver": top_dimension_driver,
        "risk_signals": risk_signals,
        "facts_packets": facts_packets,
        "period_label": label,
    }


def render_sidebar() -> str:
    st.sidebar.title(config.APP_TITLE)
    st.sidebar.caption("Local analytics, AI-narrated. Your raw data never leaves this server.")

    with st.sidebar.expander("Settings", expanded=False):
        key_display = mask_secret(config.OPENROUTER_API_KEY) if config.OPENROUTER_API_KEY else "not set"
        st.write(f"OpenRouter API key: `{key_display}`")
        st.caption("Set OPENROUTER_API_KEY as an environment variable — never entered or logged here.")
        model = st.text_input("Model", value=config.DEFAULT_MODEL)
        allow_paid = st.toggle("Allow paid models", value=config.ALLOW_PAID_MODELS_DEFAULT)
        st.caption(
            "Free-tier `:free` models only by default unless 'Allow paid models' is enabled. "
            "[OpenRouter privacy docs](https://openrouter.ai/docs)"
        )

        status = get_status()
        st.progress(
            min(1.0, status.calls_made_today / max(1, status.daily_budget)),
            text=f"LLM calls today: {status.calls_made_today}/{status.daily_budget}",
        )

    return model, allow_paid


def render_export_button(pipeline_result: dict) -> None:
    st.sidebar.divider()
    if st.sidebar.button("Prepare PPTX export", key="prepare_pptx_btn"):
        figures = st.session_state.get("_export_figures", {})
        kpis = pipeline_result["kpis"]
        profile = pipeline_result.get("profile")

        exec_tiles = [
            KPITileData(
                label=name.replace("_", " ").title(),
                value_display=format_number(k.current_value, k.unit),
                delta_display=format_pct_change(k.pct_change, with_sign=True),
                delta_positive=(
                    k.pct_change is not None
                    and not math.isinf(k.pct_change)
                    and k.pct_change >= 0
                ),
            )
            for name, k in kpis.items()
        ]

        drivers_table = None
        top_driver = pipeline_result.get("top_dimension_driver")
        if top_driver is not None:
            drivers_table = [
                {"category": c.category, "contribution_pct": c.contribution_pct}
                for c in (top_driver.top_positive + top_driver.top_negative)
            ]

        if profile and profile.profile_type == PROFILE_SNAPSHOT:
            views = [
                ViewExportData(
                    view_name="Executive Summary",
                    kpi_tiles=exec_tiles[:4],
                    trend_figure=figures.get("Executive_trend"),
                ),
            ]
            if figures.get("Snapshot_breakdown") is not None:
                views.append(
                    ViewExportData(
                        view_name="Rankings & Breakdown",
                        kpi_tiles=exec_tiles[4:8] if len(exec_tiles) > 4 else [],
                        trend_figure=figures.get("Snapshot_breakdown"),
                    )
                )
            if figures.get("Snapshot_composition") is not None:
                views.append(
                    ViewExportData(
                        view_name="Composition & Distribution",
                        kpi_tiles=[],
                        trend_figure=figures.get("Snapshot_composition"),
                    )
                )

        elif profile and profile.profile_type == PROFILE_PROJECT:
            views = [
                ViewExportData(
                    view_name="Portfolio Overview",
                    kpi_tiles=exec_tiles[:5],
                    trend_figure=figures.get("Executive_trend"),
                ),
            ]
            if figures.get("Project_gantt") is not None:
                views.append(
                    ViewExportData(
                        view_name="Timeline Schedule (Gantt)",
                        kpi_tiles=[],
                        trend_figure=figures.get("Project_gantt"),
                    )
                )
            if figures.get("Project_workload") is not None:
                views.append(
                    ViewExportData(
                        view_name="Team Workload Distribution",
                        kpi_tiles=[],
                        trend_figure=figures.get("Project_workload"),
                    )
                )

        else:
            primary_name = pipeline_result.get("primary_metric", "Revenue")
            views = [
                ViewExportData(
                    view_name="Executive",
                    kpi_tiles=exec_tiles[:4],
                    trend_figure=figures.get("Executive_trend"),
                ),
                ViewExportData(
                    view_name=primary_name.replace("_", " ").title() if primary_name else "Primary Metric",
                    kpi_tiles=exec_tiles[:1],
                    trend_figure=figures.get("Revenue_trend"),
                    drivers_table=drivers_table,
                    drivers_figure=figures.get("Revenue_drivers"),
                ),
            ]

        raw_risks = pipeline_result.get("risk_signals", [])
        risk_dicts = [
            {"description": s["description"], "severity": s.get("severity", "medium")}
            if isinstance(s, dict)
            else {"description": getattr(s, "description", str(s)), "severity": getattr(s, "severity", "medium")}
            for s in raw_risks
        ]

        period_covered = pipeline_result.get("period_label", "n/a")
        deck_inputs = DeckInputs(
            dataset_name=st.session_state.get("dataset_name", "uploaded.csv"),
            period_covered=period_covered,
            views=views,
            risk_signals=risk_dicts,
        )

        try:
            buffer = build_deck(deck_inputs)
            st.session_state["_pptx_buffer"] = buffer.getvalue()
        except Exception as exc:  # noqa: BLE001 - surface a friendly error, never crash the app
            st.sidebar.error(f"Could not build the PPTX: {exc}")

    if "_pptx_buffer" in st.session_state:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
        st.sidebar.download_button(
            "Download PPTX",
            data=st.session_state["_pptx_buffer"],
            file_name=f"aivara_insight_{timestamp}.pptx",
            mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            key="download_pptx_btn",
        )


def main() -> None:
    model, allow_paid = render_sidebar()

    ready = render_upload_page()
    if not ready:
        return

    pipeline_result = run_pipeline()
    profile = pipeline_result["profile"]

    if profile.profile_type == PROFILE_SNAPSHOT:
        tabs = st.tabs(["Executive Summary", "Rankings & Breakdown", "Composition", "Charts", "Risk & Outliers"])
        with tabs[0]:
            render_snapshot_executive_view(
                pipeline_result["df"], pipeline_result["snapshot_res"], profile, model, allow_paid=allow_paid
            )
        with tabs[1]:
            render_snapshot_breakdown_view(
                pipeline_result["df"], pipeline_result["snapshot_res"], profile
            )
        with tabs[2]:
            render_snapshot_composition_view(
                pipeline_result["df"], pipeline_result["snapshot_res"], profile
            )
        with tabs[3]:
            render_chart_explorer(pipeline_result["df"])
        with tabs[4]:
            render_snapshot_outliers_view(pipeline_result["snapshot_res"])

    elif profile.profile_type == PROFILE_PROJECT:
        tabs = st.tabs(["Portfolio Overview", "Timeline (Gantt)", "Team & Workload", "Charts", "At-Risk Tasks"])
        with tabs[0]:
            render_project_overview_view(
                pipeline_result["df"], pipeline_result["project_res"], profile, model, allow_paid=allow_paid
            )
        with tabs[1]:
            render_project_gantt_view(
                pipeline_result["df"], pipeline_result["project_res"], profile
            )
        with tabs[2]:
            render_project_team_view(
                pipeline_result["df"], pipeline_result["project_res"], profile
            )
        with tabs[3]:
            render_chart_explorer(pipeline_result["df"])
        with tabs[4]:
            render_project_at_risk_view(pipeline_result["project_res"])

    else:
        tabs = st.tabs(config.APP_TABS)
        with tabs[0]:
            render_executive_view(pipeline_result, model, allow_paid=allow_paid)
        with tabs[1]:
            render_primary_metric_view(pipeline_result, model, allow_paid=allow_paid)
        with tabs[2]:
            render_drivers_view(pipeline_result, key_prefix="operations")
        with tabs[3]:
            st.header("Customers")
            st.caption("Customer-shaped dimension breakdowns appear here when a customer-like dimension is detected.")
            customer_drivers = [
                d for d in pipeline_result.get("all_dimension_drivers", [])
                if re.search(r"(customer|client|account|user|buyer|consumer|segment)", d.dimension, re.I)
            ]
            if customer_drivers:
                cust_pipeline_result = {**pipeline_result, "all_dimension_drivers": customer_drivers}
                render_drivers_view(cust_pipeline_result, key_prefix="customers")
            else:
                st.info("No customer-shaped dimension columns (e.g. customer, client, account, user) were detected in this dataset.")
        with tabs[4]:
            render_chart_explorer(pipeline_result.get("df", st.session_state.get("dataset", __import__("pandas").DataFrame())))
        with tabs[5]:
            render_risk_view(pipeline_result)
        with tabs[6]:
            render_forecast_stub()

    render_export_button(pipeline_result)


if __name__ == "__main__":
    main()
