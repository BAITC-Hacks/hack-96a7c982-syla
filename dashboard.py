from __future__ import annotations

import html
import hashlib
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard_ui.data import (ReportError, campaign_rows, channel_label, confidence_label, export_csv, export_json,
                               export_markdown, load_report_bytes, load_report_file, selected_campaign,
                               spend_breakdown)
from dashboard_ui.runner import run_local

st.set_page_config(page_title="Beeline Campaign AI", page_icon=None, layout="wide", initial_sidebar_state="collapsed")
st.markdown("""<style>
:root{color-scheme:dark} .stApp{background:#0B0D10;color:#F4F6F8} .block-container{max-width:1600px;padding:1.25rem 2rem 3rem}
h1,h2,h3{letter-spacing:-.025em} h1{font-size:1.65rem!important;margin:0!important} h2{font-size:1.16rem!important}
[data-testid="stMetric"]{background:#15181E;border:1px solid #2A303A;border-radius:14px;padding:14px 16px;min-height:118px;transition:border-color .15s}
[data-testid="stMetric"]:hover{border-color:#555e6c}[data-testid="stMetricValue"]{font-variant-numeric:tabular-nums;font-size:1.55rem}
.kpi{background:#15181E;border:1px solid #2A303A;border-radius:14px;padding:14px 16px;min-height:132px}.kpi-label{color:#A6AFBD;font-size:.82rem}.kpi-value{font-size:1.42rem;font-weight:650;font-variant-numeric:tabular-nums;margin:5px 0}.kpi-note{color:#A6AFBD;font-size:.72rem;min-height:30px}.track{height:4px;background:#2A303A;border-radius:9px;margin-top:9px;overflow:hidden}.fill{height:100%;background:#FFD400;border-radius:9px}
.meta{color:#A6AFBD;font-size:.83rem;text-align:right}.subtitle,.fine{color:#A6AFBD}.notice{border:1px solid #3e3a21;background:#1d1b12;border-radius:12px;padding:10px 14px;color:#d8d3b5;font-size:.86rem}
.panel{background:#15181E;border:1px solid #2A303A;border-radius:14px;padding:16px;margin-bottom:12px}.eyebrow{color:#FFD400;font-size:.72rem;font-weight:700;letter-spacing:.09em;text-transform:uppercase}
.detail-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.datum{background:#1B2028;border-radius:10px;padding:12px}.datum b{display:block;font-size:1.05rem;margin-top:5px;font-variant-numeric:tabular-nums}.datum span{color:#A6AFBD;font-size:.76rem}
div.stButton>button[kind="primary"]{background:#FFD400!important;color:#111318!important;border:1px solid #FFD400!important;font-weight:750} div.stButton>button[kind="primary"]:hover{background:#ffe044!important;border-color:#ffe044!important;color:#070809!important} div.stButton>button[kind="primary"]:disabled{background:#6b611f!important;color:#c8c3a6!important;border-color:#6b611f!important} div.stButton>button:focus-visible,a:focus-visible{outline:3px solid #F4F6F8!important;outline-offset:2px}
[data-testid="stDataFrame"]{font-size:.92rem}[data-testid="stDataFrame"] div[role="columnheader"]{font-size:.82rem;font-weight:700}
.demo-banner{border-left:4px solid #FFD400;background:#24200e;padding:10px 14px;border-radius:8px;font-size:.82rem;font-weight:700;letter-spacing:.03em}
.presentation-kpi{min-height:145px}.presentation-kpi .kpi-value{font-size:1.7rem}.why{border-left:3px solid #FFD400;padding:2px 0 2px 12px;margin:10px 0;color:#D9DEE5}
@media(max-width:600px){.block-container{padding:1rem}.meta{text-align:left}.detail-grid{grid-template-columns:repeat(2,minmax(0,1fr))}[data-testid="stHorizontalBlock"]{flex-wrap:wrap}[data-testid="stColumn"]{min-width:100%!important}}
</style>""", unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def cached_report(data: bytes, source: str):
    """Cache by immutable file contents, so changed reports invalidate naturally."""
    return load_report_bytes(data, source)


def fmt_num(x, suffix=""):
    return "Нет данных" if x is None else f"{x:,.0f}".replace(",", " ") + suffix


def kpi_card(label, value, note, current=None, limit=None, presentation=False):
    progress = ""
    if current is not None and limit:
        pct = max(0, min(100, current / limit * 100))
        progress = f'<div class="track" title="{pct:.1f}% лимита"><div class="fill" style="width:{pct:.1f}%"></div></div>'
    css_class = "kpi presentation-kpi" if presentation else "kpi"
    st.markdown(f'<div class="{css_class}"><div class="kpi-label">{html.escape(label)}</div><div class="kpi-value">{html.escape(value)}</div><div class="kpi-note">{html.escape(note)}</div>{progress}</div>', unsafe_allow_html=True)


if "report" not in st.session_state:
    try: st.session_state.report = load_report_file("demo/dashboard_report.json", "demo")
    except ReportError: st.session_state.report = None
if "load_error" not in st.session_state: st.session_state.load_error = None
if "technical_error" not in st.session_state: st.session_state.technical_error = None
if "last_report" not in st.session_state: st.session_state.last_report = None
if "source_mode" not in st.session_state: st.session_state.source_mode = "Демо"
if "presentation_mode" not in st.session_state: st.session_state.presentation_mode = False
if "calculation_running" not in st.session_state: st.session_state.calculation_running = False
if st.session_state.get("pending_source_mode"):
    st.session_state.source_mode = st.session_state.pop("pending_source_mode")

head, meta = st.columns([3, 2])
with head:
    st.title("Beeline Campaign AI")
    st.markdown('<div class="subtitle">Планирование тарифных кампаний</div>', unsafe_allow_html=True)
with meta:
    report = st.session_state.report
    if report:
        bits = [report["source_label"]]
        if report.get("source") not in (None, report.get("source_kind")): bits.append(f"source {report['source']}")
        if report.get("seed") is not None: bits.append(f"seed {report['seed']}")
        if report.get("agent_commit"): bits.append(f"agent {report['agent_commit'][:12]}")
        if report.get("generated_at"): bits.append(report["generated_at"][:19].replace("T", " ") + " UTC")
        st.markdown(f'<div class="meta">{" · ".join(map(html.escape, bits))}</div>', unsafe_allow_html=True)

presentation = st.session_state.presentation_mode
control_widths = [1.2, 1.2] if presentation else [1.2, 1.2, 2.5, 1.2]
control_cols = st.columns(control_widths)
mode_col = control_cols[0]
with mode_col:
    mode = st.segmented_control("Источник", ["Отчёт", "Демо"], key="source_mode", label_visibility="collapsed")
if mode == "Демо" and (not report or not report["is_demo"]):
    if report: st.session_state.last_report = report
    try: st.session_state.report = load_report_file("demo/dashboard_report.json", "demo"); st.rerun()
    except ReportError as exc: st.session_state.load_error = str(exc)
elif mode == "Отчёт" and report and report["is_demo"]:
    st.session_state.report = st.session_state.last_report
    st.rerun()
if not presentation:
  with control_cols[1]:
    if st.button("Запустить расчёт", type="primary", use_container_width=True, help="Только локальная симуляция", disabled=st.session_state.calculation_running):
        previous = st.session_state.report
        st.session_state.calculation_running = True
        try:
            with st.status("Выполняется локальный расчёт", expanded=True) as status:
                st.session_state.report = run_local(42)
                st.session_state.last_report = st.session_state.report
                st.session_state.pending_source_mode = "Отчёт"
                status.update(label="Расчёт завершён", state="complete")
            st.session_state.load_error = None; st.rerun()
        except Exception as exc:
            st.session_state.report = previous; st.session_state.load_error = "Локальный расчёт не выполнен. Последний успешный отчёт сохранён."
            st.session_state.technical_error = repr(exc)
        finally:
            st.session_state.calculation_running = False
  with control_cols[2]:
    upload = st.file_uploader("Загрузить отчёт JSON (до 5 МБ)", type=["json"], label_visibility="collapsed")
    upload_data = upload.getvalue() if upload is not None else None
    upload_key = (upload.name, hashlib.sha256(upload_data).hexdigest()) if upload is not None else None
    if upload is not None and st.session_state.get("upload_key") != upload_key:
        previous = st.session_state.report
        try:
            st.session_state.report = cached_report(upload_data, "upload")
            st.session_state.last_report = st.session_state.report
            st.session_state.pending_source_mode = "Отчёт"
            st.session_state.upload_key = upload_key; st.session_state.load_error = None
            st.session_state.technical_error = None; st.rerun()
        except ReportError as exc:
            st.session_state.report = previous; st.session_state.upload_key = upload_key
            st.session_state.load_error = str(exc)
  present_col = control_cols[3]
else:
  present_col = control_cols[1]
with present_col:
    st.toggle("Режим презентации", key="presentation_mode")

if st.session_state.load_error:
    suffix = " Показан последний успешно загруженный отчёт." if st.session_state.report else ""
    st.error(st.session_state.load_error + suffix + " Загрузите JSON, повторите расчёт или включите демо.")
    if st.session_state.get("technical_error"):
        with st.expander("Техническая информация"):
            st.code(st.session_state.technical_error)
report = st.session_state.report
if not report:
    st.info("Нет отчёта. Загрузите JSON, запустите локальный расчёт или включите демо."); st.stop()

st.markdown('<div class="notice">Синтетические данные. Результаты симуляции не являются показателями Beeline.</div>', unsafe_allow_html=True)
if report["is_demo"]:
    st.markdown('<div class="demo-banner">ДЕМОНСТРАЦИОННЫЕ ДАННЫЕ — не результат текущего агента</div>', unsafe_allow_html=True)
elif report["source_kind"] == "upload":
    st.caption("Просмотр загруженного отчёта. Кнопка расчёта использует установленную локальную версию agent.py, а не код из отчёта.")
elif report["source_kind"] == "local":
    st.caption("Результат рассчитан установленной локальной версией агента.")
m = report["metrics"]
cols = st.columns(5)
labels = [("Чистый прирост", fmt_num(m["net_lift"], " у.е."), "Итог score после расходов", None),
          ("ROI", "Нет данных" if m["cost"] == 0 else (f"{m['roi']:.2f}×" if m["roi"] is not None else "Нет данных"), "Gross lift / расходы" if m["cost"] != 0 else "Не определён: расходы равны 0"),
          ("Расходы", f'{fmt_num(m["cost"])} / 100 000 у.е.' if m["cost"] is not None else "Нет данных", "Итого, включая пилоты", (m["cost"], 100000)),
          ("Контакты", f'{fmt_num(m["contacts"])} / 15 000' if m["contacts"] is not None else "Нет данных", "Итого, включая пилоты", (m["contacts"], 15000)),
          ("Финальные кампании", f'{m["campaigns"]} / 10', "Рекомендации, без пилотов", (m["campaigns"], 10))]
labels[0] = (*labels[0][:3], None)
labels[1] = (*labels[1], None)
for col, (label, value, help_text, progress) in zip(cols, labels):
    with col: kpi_card(label, value, help_text, *(progress or (None, None)), presentation=presentation)

tab_plan, tab_pilots = st.tabs(["План кампаний", "Результаты пилотов"])
with tab_plan:
    left, right = st.columns([2, 1], gap="large")
    with left:
        st.subheader("Рекомендованные кампании")
        channels = sorted({c["channel"] for c in report["campaigns"] if c["channel"]})
        selected_channels = st.multiselect("Канал", channels, default=channels, placeholder="Все каналы")
        rows = campaign_rows(report, selected_channels)
        table = pd.DataFrame([{"Сегмент":c["arpu_segment"] or c["data_segment"] or c["call_segment"] or "—", "Переход":f"{c['current_tariff'] or '—'} → {c['target_tariff'] or '—'}", "Канал":channel_label(c["channel"]), "Контакты":c["contacts"], "Ожид. прирост, у.е.":c["expected_net_effect"], "Уверенность":confidence_label(c), "Пилоты":c["pilots_used"]} for c in rows])
        if table.empty:
            st.info("Финальных кампаний нет. Пустой план не готов к официальной сдаче.")
            chosen = None
        else:
            styled = table.style.map(lambda value: "color:#ff7373;font-weight:700" if isinstance(value, (int, float)) and value < 0 else "", subset=["Ожид. прирост, у.е."])
            event = st.dataframe(styled, hide_index=True, use_container_width=True, height=318, row_height=42, on_select="rerun", selection_mode="single-row",
                                 column_config={"Ожид. прирост, у.е.":st.column_config.NumberColumn("Ожид. прирост, у.е.", help="Модельный денежный эффект кампании после расходов; не итог score", format="%.0f"), "Уверенность":st.column_config.TextColumn(help="UI-индикатор положения и ширины 95% модельного интервала; не вероятность успеха")})
            chosen = selected_campaign(rows, event.selection.rows)
    with right:
        st.subheader("Распределение бюджета")
        breakdown = spend_breakdown(report)
        st.caption(
            f"Расходы — финал: {fmt_num(breakdown['final_cost'], ' у.е.')} · "
            f"пилоты: {fmt_num(breakdown['pilot_cost'], ' у.е.')} · всего: {fmt_num(breakdown['total_cost'], ' у.е.')}\n\n"
            f"Контакты — финал: {fmt_num(breakdown['final_contacts'])} · "
            f"пилоты: {fmt_num(breakdown['pilot_contacts'])} · всего: {fmt_num(breakdown['total_contacts'])}"
        )
        aggregate = {}
        for c in report["campaigns"]:
            ch = c["channel"] or "не указан"; aggregate.setdefault(ch, [0, 0]); aggregate[ch][0] += c["cost"] or 0; aggregate[ch][1] += c["contacts"] or 0
        if aggregate:
            names=list(aggregate); display_names=[channel_label(x) for x in names]
            for values, title, color in [([aggregate[x][0] for x in names], "Расходы, у.е.", "#FFD400"),
                                         ([aggregate[x][1] for x in names], "Контакты", "#697386")]:
                fig=go.Figure(go.Bar(y=display_names,x=values,orientation="h",marker_color=color,
                                     text=[f"{v:,.0f}".replace(",", " ") for v in values],textposition="auto"))
                fig.update_layout(height=175,margin=dict(l=0,r=8,t=28,b=4),title=dict(text=title,font_size=13),
                                  paper_bgcolor="#0B0D10",plot_bgcolor="#0B0D10",font_color="#A6AFBD",
                                  showlegend=False,xaxis_gridcolor="#2A303A",yaxis_autorange="reversed")
                st.plotly_chart(fig,use_container_width=True,config={"displayModeBar":False})
            st.caption("Только финальные кампании. Контакты показаны отдельно, включая бесплатный Push.")
        else: st.info("Нет данных для распределения бюджета.")
    if chosen:
        st.subheader("Выбранная кампания")
        ci=chosen["ci95"]; interval="Нет данных" if ci[0] is None or ci[1] is None else f"{ci[0]*100:.1f}% … {ci[1]*100:.1f}%"
        explanation=f"Сегмент {chosen['arpu_segment'] or chosen['data_segment'] or chosen['call_segment'] or 'не указан'} → тариф {chosen['target_tariff'] or 'не указан'} → канал {channel_label(chosen['channel'])} → {fmt_num(chosen['contacts'])} контактов. "
        if chosen["posterior_lift"] is not None: explanation += f"Оценка относительного эффекта: {chosen['posterior_lift']*100:.1f}%. "
        if ci[0] is not None: explanation += "Модельный интервал включает ноль." if ci[0] <= 0 <= ci[1] else "Модельный интервал не включает ноль."
        st.markdown(f'<div class="panel"><div class="eyebrow">{html.escape(chosen["name"])}</div><h3>{html.escape(chosen["current_tariff"] or "—")} → {html.escape(chosen["target_tariff"] or "—")}</h3><p class="fine">{html.escape(explanation)}</p><div class="detail-grid">'
                    f'<div class="datum"><span>Группа / охват</span><b>{fmt_num(chosen["group_size"])} / {fmt_num(chosen["contacts"])}</b></div><div class="datum"><span>Канал / расходы</span><b>{html.escape(channel_label(chosen["channel"]))} / {fmt_num(chosen["cost"], " у.е.")}</b></div>'
                    f'<div class="datum"><span>Ожидаемый эффект</span><b>{fmt_num(chosen["expected_net_effect"], " у.е.")}</b></div><div class="datum"><span>Posterior lift</span><b>{"Нет данных" if chosen["posterior_lift"] is None else f"{chosen["posterior_lift"]*100:.1f}%"}</b></div>'
                    f'<div class="datum"><span>Стандартное отклонение</span><b>{"Нет данных" if chosen["std"] is None else f"{chosen["std"]*100:.1f}%"}</b></div><div class="datum"><span>95% модельный интервал</span><b>{interval}</b></div>'
                    f'<div class="datum"><span>Risk-adjusted lift</span><b>{"Нет данных" if chosen["risk_adjusted_lift"] is None else f"{chosen["risk_adjusted_lift"]*100:.1f}%"}</b></div><div class="datum"><span>Пилоты / контакты</span><b>{fmt_num(chosen["pilots_used"])} / {fmt_num(chosen["pilot_customers"])}</b></div></div></div>',unsafe_allow_html=True)
        reasons = []
        if chosen["pilots_used"] is not None and chosen["posterior_lift"] is not None:
            direction = "положительной" if chosen["posterior_lift"] > 0 else "неположительной"
            reasons.append(f"После {chosen['pilots_used']} пилотов оценка эффекта остаётся {direction}.")
        if ci[0] is not None and ci[0] > 0: reasons.append("Нижняя граница модельного интервала находится выше нуля.")
        if chosen["contacts"] is not None: reasons.append(f"Кампания использует {channel_label(chosen['channel'])} и охватывает {fmt_num(chosen['contacts'])} абонентов.")
        if reasons: st.markdown('<div class="why"><b>Почему рекомендация попала в план</b><br>' + " ".join(map(html.escape, reasons)) + '</div>', unsafe_allow_html=True)
        st.caption("Relative lift — модельная относительная оценка в процентах. Ожидаемый эффект — денежная оценка в условных единицах; общий Net Lift берётся только из итогового score.")
        warnings=list(chosen["warnings"])
        if ci[0] is not None and ci[0] <= 0 <= ci[1]: warnings.append("Эффект ещё неопределён")
        if ci[1] is not None and ci[1] < 0: warnings.append("Высокий риск отрицательного эффекта")
        if chosen["expected_net_effect"] is not None and chosen["expected_net_effect"] < 0: warnings.append("Отрицательный ожидаемый эффект — требуется проверка")
        for warning in warnings: st.warning(warning)
with tab_pilots:
    st.subheader("Результаты пилотов")
    st.caption("Пилоты — небольшие кампании, которыми агент уточняет эффект для текущей аудитории. Результат содержит выборочный шум; агент объединяет его с prior.")
    if report["pilots"]:
        st.caption(f"Выполнено пилотов: {len(report['pilots'])} · контактов: {sum(p['contacts'] or 0 for p in report['pilots']):,} · расходы: {sum(p['cost'] or 0 for p in report['pilots']):,.0f} у.е.")
        pilot_table = pd.DataFrame([{"Пилот":p["pilot"], "Сегмент":p["segment"], "Целевой тариф":p["target_tariff"], "Канал":channel_label(p["channel"]),
                                    "Контакты":p["contacts"], "Расходы, у.е.":p["cost"], "Наблюдаемый lift":p["observed_lift_ratio"],
                                    "Наблюдаемый эффект":p["observed_lift_total"], "Статус":p["status"]} for p in report["pilots"]])
        if pilot_table["Статус"].isna().all(): pilot_table = pilot_table.drop(columns=["Статус"])
        pilot_table["Наблюдаемый lift"] = pilot_table["Наблюдаемый lift"] * 100
        st.dataframe(pilot_table, hide_index=True, use_container_width=True, row_height=40,
                     column_config={"Наблюдаемый lift": st.column_config.NumberColumn(format="%.1f%%")})
    else: st.info("В этом отчёте нет подробной истории пилотов.")

if not presentation:
    with st.expander("Экспорт"):
        st.caption("UI-экспорт не является официальным submission.csv. Официальная сдача формируется make_submission.py.")
        a,b,c=st.columns(3)
        a.download_button("CSV кампаний",export_csv(report),"campaign_plan.csv","text/csv",use_container_width=True)
        b.download_button("JSON отчёта",export_json(report),"dashboard_report.json","application/json",use_container_width=True)
        c.download_button("Markdown резюме",export_markdown(report),"campaign_summary.md","text/markdown",use_container_width=True)
