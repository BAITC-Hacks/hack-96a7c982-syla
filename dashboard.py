"""Beeline Campaign AI: report viewer and explicitly triggered local simulation."""
from __future__ import annotations

import hashlib
import html
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard_ui.data import (
    MAX_UPLOAD_BYTES, ReportError, campaign_rows, channel_label, channel_totals,
    confidence_label, export_csv, export_json, export_markdown, load_report_bytes,
    load_report_file, selected_campaign, spend_breakdown,
)
from dashboard_ui.process import run_bounded

ROOT = Path(__file__).resolve().parent
st.set_page_config(page_title="Beeline Campaign AI", layout="wide", initial_sidebar_state="collapsed")
st.markdown("""<style>
.stApp{background:#0B0D10;color:#F4F6F8}.block-container{max-width:1600px;padding:1.4rem 2rem 3rem}
h1{font-size:1.65rem!important;margin:0!important;letter-spacing:-.03em}h2,h3{letter-spacing:-.02em}
.kpi{height:160px;background:#15181E;border:1px solid #2A303A;border-radius:14px;padding:16px;overflow-wrap:anywhere}
.kpi span,.meta,.fine{color:#A6AFBD}.kpi span{font-size:.86rem}.kpi b{display:block;font-size:1.5rem;margin:8px 0;font-variant-numeric:tabular-nums}.kpi small{color:#A6AFBD}
.track{height:5px;background:#2A303A;border-radius:4px;margin-top:12px}.fill{height:5px;background:#FFD400;border-radius:4px}
.notice{border:1px solid #514925;background:#211e11;border-radius:10px;padding:12px 16px;color:#E4DEC3;margin:12px 0}
.panel{border:1px solid #2A303A;background:#15181E;border-radius:14px;padding:20px;margin:8px 0}.details{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.datum{background:#1B2028;padding:12px;border-radius:10px;overflow-wrap:anywhere}.datum small{color:#A6AFBD}.datum b{display:block;margin-top:6px;font-variant-numeric:tabular-nums}
button[kind="primary"],button[kind="primary"] p{background:#FFD400!important;color:#111318!important;font-weight:700!important}button[kind="primary"]:hover{filter:brightness(1.08)}button:focus-visible{outline:3px solid #F4F6F8!important;outline-offset:2px}button[kind="primary"]:disabled{opacity:.55}
.meta{text-align:right;overflow-wrap:anywhere;font-size:.85rem}.presentation .kpi b{font-size:1.8rem}
@media(max-width:700px){.block-container{padding:1rem}.meta{text-align:left}.details{grid-template-columns:repeat(2,minmax(0,1fr))}[data-testid="stHorizontalBlock"]{flex-wrap:wrap}[data-testid="stColumn"]{min-width:100%!important}.kpi{height:auto;min-height:130px}}
</style>""", unsafe_allow_html=True)


@st.cache_data(show_spinner=False, max_entries=8)
def cached_report(content: bytes, source: str) -> dict:
    return load_report_bytes(content, source)


def number(value, suffix="") -> str:
    return "Нет данных" if value is None else f"{value:,.0f}".replace(",", " ") + suffix


def percent(value) -> str:
    return "Нет данных" if value is None else f"{value * 100:.1f}%"


def card(label, value, note, current=None, limit=None):
    progress = ""
    if current is not None and limit:
        width = max(0.0, min(100.0, current / limit * 100))
        color = "#ff7373" if current > limit else "#FFD400"
        progress = f'<div class="track"><div class="fill" style="width:{width:.1f}%;background:{color}"></div></div>'
    st.markdown(f'<div class="kpi"><span>{html.escape(label)}</span><b>{html.escape(value)}</b><small>{html.escape(note)}</small>{progress}</div>', unsafe_allow_html=True)


state = st.session_state
for key, default in {"loaded_report": None, "load_error": None, "processed_upload": None,
                     "source_mode": "Демо", "presentation": False, "calculation_running": False, "run_requested": False}.items():
    if key not in state:
        state[key] = default
if state.get("pending_mode"):
    state.source_mode = state.pop("pending_mode")

head, right = st.columns([3, 2])
with head:
    st.title("Beeline Campaign AI")
    st.caption("Планирование тарифных кампаний")
with right:
    st.toggle("Режим презентации", key="presentation")

controls = st.columns([1.1, 1.2, 2.4])
with controls[0]:
    st.radio("Источник", ["Демо", "Отчёт"], key="source_mode", horizontal=True, label_visibility="collapsed")
# Mode and provenance are independent: a uploaded demo stays visibly demo,
# but does not trigger an infinite switch/rerun between Demo and Report.
if not state.presentation:
    with controls[1]:
        def request_run():
            if not state.calculation_running:
                state.calculation_running = True
                state.run_requested = True
        st.button("Запустить расчёт", type="primary", use_container_width=True,
                  help="Локальная версия агента, seed 42; не реальные коммуникации",
                  disabled=state.calculation_running, on_click=request_run)
    with controls[2]:
        upload = st.file_uploader("Загрузить JSON, до 5 МБ", type=["json"], label_visibility="collapsed")
        if upload is None:
            state.processed_upload = None
        elif upload.size > MAX_UPLOAD_BYTES:
            state.load_error = "Файл больше 5 МБ. Предыдущий отчёт сохранён."
        else:
            content = upload.getvalue()
            digest = hashlib.sha256(content).hexdigest()
            if digest != state.processed_upload:
                state.processed_upload = digest
                try:
                    state.loaded_report = cached_report(content, "upload")
                    state.pending_mode = "Отчёт"
                    state.load_error = None
                    st.rerun()
                except ReportError as exc:
                    state.load_error = str(exc)
    with st.expander("Открыть существующий PITCH_REPORT.json"):
        if st.button("Прочитать локальный отчёт"):
            try:
                state.loaded_report = load_report_file(ROOT / "PITCH_REPORT.json", "upload")
                state.pending_mode = "Отчёт"
                state.load_error = None
                st.rerun()
            except ReportError as exc:
                state.load_error = str(exc)

if state.run_requested:
    state.run_requested = False
    try:
        with st.spinner("Локальный расчёт — не более 9 минут…"):
            state.loaded_report = run_bounded(42)
        state.pending_mode = "Отчёт"
        state.load_error = None
    except ReportError as exc:
        state.load_error = str(exc)
    except Exception:
        state.load_error = "Расчёт не выполнен. Предыдущий отчёт сохранён. Проверьте данные и зависимости."
    finally:
        state.calculation_running = False
    st.rerun()

if state.presentation:
    st.markdown("<style>.kpi b{font-size:1.8rem}</style>", unsafe_allow_html=True)

if state.load_error:
    st.error(state.load_error + " Можно загрузить другой JSON или выбрать демо.")
if state.source_mode == "Демо":
    try:
        report = cached_report((ROOT / "demo/dashboard_report.json").read_bytes(), "demo")
    except (OSError, ReportError):
        st.error("Демонстрационный отчёт недоступен. Выберите «Отчёт» и загрузите JSON.")
        st.stop()
else:
    report = state.loaded_report
if report is None:
    st.info("Отчёт ещё не загружен. Загрузите JSON, прочитайте PITCH_REPORT.json или запустите расчёт.")
    st.stop()

bits = [report["source_label"]]
if report.get("seed") is not None:
    bits.append(f"seed {report['seed']}")
if report.get("agent_commit"):
    bits.append("agent " + report["agent_commit"][:12])
if report.get("generated_at"):
    bits.append(report["generated_at"])
st.caption(" · ".join(bits))
notice = "ДЕМОНСТРАЦИОННЫЕ ДАННЫЕ — не результат текущего агента. " if report["is_demo"] else ""
st.markdown('<div class="notice">' + notice + 'Синтетические данные. Результаты симуляции не являются показателями Beeline.</div>', unsafe_allow_html=True)
if report["source_kind"] == "upload":
    st.caption("Просмотр загруженного отчёта. Новый расчёт использует локальный agent.py, а не версию из загруженного файла.")
for warning in report.get("warnings", []):
    st.warning(warning)
m = report["metrics"]
roi = "Не определён" if m["cost"] == 0 else "Нет данных" if m["roi"] is None else f"{m['roi']:.2f}×"
values = [("Чистый прирост", number(m["net_lift"], " у.е."), "Итог score после расходов", None, None),
          ("ROI", roi, "Gross lift / расходы; не гарантия доходности", None, None),
          ("Расходы", number(m["cost"], " у.е."), "Из 100 000 у.е. · включая пилоты", m["cost"], 100000),
          ("Контакты", number(m["contacts"]), "Из 15 000 · включая пилоты", m["contacts"], 15000),
          ("Кампании", str(m["campaigns"]), "Из 10 финальных кампаний", m["campaigns"], 10)]
for col, args in zip(st.columns(5), values):
    with col:
        card(*args)

tab_plan, tab_pilots = st.tabs(["План кампаний", "Результаты пилотов"])
with tab_plan:
    left, chart = st.columns([2, 1], gap="large")
    with left:
        st.subheader("Рекомендованные кампании")
        channels = list(dict.fromkeys(c["channel"] for c in report["campaigns"]))
        report_key = hashlib.sha256(export_json(report)).hexdigest()[:16]
        selected_channels = st.multiselect("Канал", channels, default=channels, format_func=channel_label, key="channels_" + report_key)
        rows = campaign_rows(report, selected_channels)
        chosen = None
        if not rows:
            st.info("По выбранному фильтру кампаний нет." if report["campaigns"] else "Финальный план пуст.")
        else:
            table = pd.DataFrame([{"Сегмент": " / ".join(str(c[k]) for k in ("arpu_segment", "data_segment", "call_segment") if c[k]) or "—",
                                   "Переход": f"{c['current_tariff'] or '—'} → {c['target_tariff'] or '—'}",
                                   "Канал": channel_label(c["channel"]), "Контакты": c["contacts"],
                                   "Ожид. эффект, у.е.": c["expected_net_effect"], "95% интервал": confidence_label(c), "Пилоты": c["pilots_used"]} for c in rows])
            selection_key = hashlib.sha256(repr([c["id"] for c in rows]).encode()).hexdigest()[:12]
            styled = table.style.apply(
                lambda col: ["color:#ff7373;font-weight:700" if pd.notna(v) and v < 0 else "" for v in col],
                subset=["Ожид. эффект, у.е."])
            event = st.dataframe(styled, hide_index=True, use_container_width=True, height=330,
                                 on_select="rerun", selection_mode="single-row", key=f"table_{report_key}_{selection_key}",
                                 column_config={"Ожид. эффект, у.е.": st.column_config.NumberColumn(format="%.0f", help="Прогноз кампании; не общий итог score"),
                                                "95% интервал": st.column_config.TextColumn(help="Положение модельного интервала относительно нуля, не вероятность успеха")})
            chosen = selected_campaign(rows, event.selection.rows)
    with chart:
        st.subheader("Бюджет и охват")
        b = spend_breakdown(report)
        st.caption(f"Расходы — финал: {number(b['final_cost'])} · пилоты: {number(b['pilot_cost'])} · всего: {number(b['total_cost'])} у.е.")
        st.caption(f"Контакты — финал: {number(b['final_contacts'])} · пилоты: {number(b['pilot_contacts'])} · всего: {number(b['total_contacts'])}")
        aggregate = channel_totals(report)
        for field, title, color in [("cost", "Финальные кампании: расходы, у.е.", "#FFD400"), ("contacts", "Финальные кампании: контакты", "#8793A4")]:
            available = [(channel_label(ch), v[field]) for ch, v in aggregate.items() if v[field] is not None]
            if len(available) != len(aggregate):
                st.caption("Неизвестные значения пропущены; график неполный.")
            if not available:
                st.info(title + ": нет данных.")
                continue
            fig = go.Figure(go.Bar(y=[v[0] for v in available], x=[v[1] for v in available], orientation="h", marker_color=color,
                                  text=[number(v[1]) for v in available], textposition="auto"))
            fig.update_layout(height=175, margin=dict(l=0, r=10, t=30, b=4), title=dict(text=title, font_size=12),
                              paper_bgcolor="#0B0D10", plot_bgcolor="#0B0D10", font=dict(color="#CBD2DC", size=13),
                              yaxis_autorange="reversed", xaxis_gridcolor="#2A303A")
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    if chosen:
        st.subheader("Выбранная кампания")
        ci = chosen["ci95"]
        interval = "Нет данных" if ci[0] is None else f"{percent(ci[0])} … {percent(ci[1])}"
        fields = [("Текущий → целевой тариф", f"{chosen['current_tariff'] or '—'} → {chosen['target_tariff'] or '—'}"),
                  ("Канал", channel_label(chosen["channel"])), ("Контакты", number(chosen["contacts"])),
                  ("Расходы", number(chosen["cost"], " у.е.")), ("Ожидаемый эффект", number(chosen["expected_net_effect"], " у.е.")),
                  ("Posterior lift", percent(chosen["posterior_lift"])), ("Стандартное отклонение", percent(chosen["std"])),
                  ("95% модельный интервал", interval), ("Risk-adjusted lift", percent(chosen["risk_adjusted_lift"])),
                  ("Пилоты / контакты", number(chosen["pilots_used"]) + " / " + number(chosen["pilot_customers"]))]
        cells = "".join(f'<div class="datum"><small>{html.escape(k)}</small><b>{html.escape(v)}</b></div>' for k, v in fields)
        st.markdown('<div class="panel"><b>' + html.escape(chosen["name"]) + '</b><div class="details">' + cells + '</div></div>', unsafe_allow_html=True)
        st.caption("Показатели приведены из отчёта, не доказывают превосходство канала. Интервал — модельная неопределённость; денежный итог всей стратегии берётся из score после дедупликации.")
        for warning in chosen["warnings"]:
            st.warning(warning)
        if chosen["expected_net_effect"] is not None and chosen["expected_net_effect"] < 0:
            st.warning("Отрицательный ожидаемый эффект — требуется проверка.")
        if ci[0] is not None and ci[0] <= 0 <= ci[1]:
            st.warning("Модельный интервал включает ноль: знак эффекта ещё неопределён.")
with tab_pilots:
    st.subheader("Результаты пилотов")
    e = report["exploration"]
    st.caption(f"Пилоты: {number(e['pilots_executed'])} / 20 · контакты: {number(e['pilot_contacts'])} · расходы: {number(e['pilot_cost'], ' у.е.')}")
    st.caption("Пилот — маленькая кампания в симуляции. Наблюдаемый эффект содержит выборочный шум.")
    if not report["pilots"]:
        st.info("В этом отчёте нет подробной истории пилотов.")
    else:
        st.dataframe(pd.DataFrame([{"Пилот": p["pilot"], "Тариф": p["target_tariff"], "Канал": channel_label(p["channel"]),
                                    "Контакты": p["contacts"], "Стоимость, у.е.": p["cost"], "Наблюдаемый lift": percent(p["observed_lift_ratio"]),
                                    "Статус из отчёта": p["status"] or "Не указан"} for p in report["pilots"]]), hide_index=True, use_container_width=True)
if not state.presentation:
    with st.expander("Экспорт текущего отчёта"):
        st.caption("Это UI-экспорт, не официальный submission.csv. Источник и seed сохраняются.")
        a, b, c = st.columns(3)
        a.download_button("CSV кампаний", export_csv(report), "campaign_plan.csv", "text/csv", use_container_width=True)
        b.download_button("JSON отчёта", export_json(report), "dashboard_report.json", "application/json", use_container_width=True)
        c.download_button("Markdown", export_markdown(report), "campaign_summary.md", "text/markdown", use_container_width=True)
