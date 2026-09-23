from __future__ import annotations

from pathlib import Path
import html
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard_ui.data import ReportError, export_csv, export_json, export_markdown, load_report_bytes, load_report_file
from dashboard_ui.runner import run_local

st.set_page_config(page_title="Beeline Campaign AI", page_icon=None, layout="wide", initial_sidebar_state="collapsed")
st.markdown("""<style>
:root{color-scheme:dark} .stApp{background:#0B0D10;color:#F4F6F8} .block-container{max-width:1600px;padding:1.25rem 2rem 3rem}
h1,h2,h3{letter-spacing:-.025em} h1{font-size:1.65rem!important;margin:0!important} h2{font-size:1.16rem!important}
[data-testid="stMetric"]{background:#15181E;border:1px solid #2A303A;border-radius:14px;padding:14px 16px;min-height:118px;transition:border-color .15s}
[data-testid="stMetric"]:hover{border-color:#555e6c}[data-testid="stMetricValue"]{font-variant-numeric:tabular-nums;font-size:1.55rem}
.meta{color:#A6AFBD;font-size:.83rem;text-align:right}.subtitle,.fine{color:#A6AFBD}.notice{border:1px solid #3e3a21;background:#1d1b12;border-radius:12px;padding:10px 14px;color:#d8d3b5;font-size:.86rem}
.panel{background:#15181E;border:1px solid #2A303A;border-radius:14px;padding:16px;margin-bottom:12px}.eyebrow{color:#FFD400;font-size:.72rem;font-weight:700;letter-spacing:.09em;text-transform:uppercase}
.detail-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.datum{background:#1B2028;border-radius:10px;padding:12px}.datum b{display:block;font-size:1.05rem;margin-top:5px;font-variant-numeric:tabular-nums}.datum span{color:#A6AFBD;font-size:.76rem}
div.stButton>button[kind="primary"]{background:#FFD400;color:#0B0D10;border-color:#FFD400;font-weight:700} div.stButton>button:focus-visible,a:focus-visible{outline:2px solid #FFD400!important;outline-offset:2px}
@media(max-width:600px){.block-container{padding:1rem}.meta{text-align:left}.detail-grid{grid-template-columns:repeat(2,minmax(0,1fr))}[data-testid="stHorizontalBlock"]{flex-wrap:wrap}[data-testid="stColumn"]{min-width:100%!important}}
</style>""", unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def cached_report(data: bytes, source: str):
    """Cache by immutable file contents, so changed reports invalidate naturally."""
    return load_report_bytes(data, source)


def fmt_num(x, suffix=""):
    return "Нет данных" if x is None else f"{x:,.0f}".replace(",", " ") + suffix


if "report" not in st.session_state:
    try: st.session_state.report = load_report_file("demo/dashboard_report.json", "demo")
    except ReportError: st.session_state.report = None
if "load_error" not in st.session_state: st.session_state.load_error = None
if "last_report" not in st.session_state: st.session_state.last_report = None
if "source_mode" not in st.session_state: st.session_state.source_mode = "Демо"
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
        if report.get("seed") is not None: bits.append(f"seed {report['seed']}")
        if report.get("generated_at"): bits.append(report["generated_at"][:19].replace("T", " ") + " UTC")
        st.markdown(f'<div class="meta">{" · ".join(map(html.escape, bits))}</div>', unsafe_allow_html=True)

mode_col, run_col, upload_col, present_col = st.columns([1.2, 1.2, 2.5, 1.2])
with mode_col:
    mode = st.segmented_control("Источник", ["Отчёт", "Демо"], key="source_mode", label_visibility="collapsed")
if mode == "Демо" and (not report or not report["is_demo"]):
    if report: st.session_state.last_report = report
    try: st.session_state.report = load_report_file("demo/dashboard_report.json", "demo"); st.rerun()
    except ReportError as exc: st.session_state.load_error = str(exc)
elif mode == "Отчёт" and report and report["is_demo"]:
    st.session_state.report = st.session_state.last_report
    st.rerun()
with run_col:
    if st.button("Запустить расчёт", type="primary", use_container_width=True, help="Только локальная симуляция"):
        previous = st.session_state.report
        try:
            with st.status("Выполняется локальный расчёт", expanded=True) as status:
                st.session_state.report = run_local(42)
                st.session_state.last_report = st.session_state.report
                st.session_state.pending_source_mode = "Отчёт"
                status.update(label="Расчёт завершён", state="complete")
            st.session_state.load_error = None; st.rerun()
        except Exception as exc:
            st.session_state.report = previous; st.session_state.load_error = f"Локальный расчёт не выполнен: {exc}"
with upload_col:
    upload = st.file_uploader("Загрузить отчёт JSON (до 5 МБ)", type=["json"], label_visibility="collapsed")
    if upload is not None and st.session_state.get("upload_key") != (upload.name, upload.size):
        previous = st.session_state.report
        try:
            st.session_state.report = cached_report(upload.getvalue(), "upload")
            st.session_state.last_report = st.session_state.report
            st.session_state.pending_source_mode = "Отчёт"
            st.session_state.upload_key = (upload.name, upload.size); st.session_state.load_error = None; st.rerun()
        except ReportError as exc:
            st.session_state.report = previous; st.session_state.load_error = str(exc)
with present_col:
    presentation = st.toggle("Режим презентации", value=False)

if st.session_state.load_error:
    suffix = " Показан последний успешно загруженный отчёт." if st.session_state.report else ""
    st.error(st.session_state.load_error + suffix + " Загрузите JSON, повторите расчёт или включите демо.")
report = st.session_state.report
if not report:
    st.info("Нет отчёта. Загрузите JSON, запустите локальный расчёт или включите демо."); st.stop()

st.markdown('<div class="notice">Синтетические данные. Результаты симуляции не являются показателями Beeline.</div>', unsafe_allow_html=True)
m = report["metrics"]
cols = st.columns(5)
labels = [("Чистый прирост", fmt_num(m["net_lift"], " у.е."), "Итог локального скорера после расходов"),
          ("ROI", "Нет данных" if m["cost"] == 0 else (f"{m['roi']:.2f}×" if m["roi"] is not None else "Нет данных"), "Gross lift / расходы" if m["cost"] != 0 else "Не определён: расходы равны 0"),
          ("Расходы", fmt_num(m["cost"], " у.е."), "Коммуникации, включая пилоты"),
          ("Контакты", fmt_num(m["contacts"]), "Суммарно, включая пилоты"),
          ("Финальные кампании", str(m["campaigns"]), "Только рекомендации, без пилотов")]
for col, (label, value, help_text) in zip(cols, labels): col.metric(label, value, help=help_text)

tab_plan, tab_pilots = st.tabs(["План кампаний", "Pilot Intelligence"])
with tab_plan:
    left, right = st.columns([2, 1], gap="large")
    with left:
        st.subheader("Рекомендованные кампании")
        channels = sorted({c["channel"] for c in report["campaigns"] if c["channel"]})
        selected_channels = st.multiselect("Канал", channels, default=channels, placeholder="Все каналы")
        rows = [c for c in report["campaigns"] if not channels or c["channel"] in selected_channels]
        table = pd.DataFrame([{"Кампания":c["name"], "Сегмент":c["arpu_segment"] or c["data_segment"] or c["call_segment"] or "—", "Переход":f"{c['current_tariff'] or '—'} → {c['target_tariff'] or '—'}", "Канал":c["channel"] or "—", "Контакты":c["contacts"], "Ожидаемый чистый эффект":c["expected_net_effect"], "Неопределённость":None if c["std"] is None else c["std"] * 100, "Пилоты":c["pilots_used"]} for c in rows])
        if table.empty:
            st.info("Финальных кампаний нет. Пустой план не готов к официальной сдаче.")
            chosen = None
        else:
            event = st.dataframe(table, hide_index=True, use_container_width=True, height=285, on_select="rerun", selection_mode="single-row",
                                 column_config={"Ожидаемый чистый эффект":st.column_config.NumberColumn(format="%.0f у.е."), "Неопределённость":st.column_config.NumberColumn(format="%.1f%%")})
            index = event.selection.rows[0] if event.selection.rows else 0
            chosen = rows[index]
    with right:
        st.subheader("Распределение бюджета")
        aggregate = {}
        for c in report["campaigns"]:
            ch = c["channel"] or "не указан"; aggregate.setdefault(ch, [0, 0]); aggregate[ch][0] += c["cost"] or 0; aggregate[ch][1] += c["contacts"] or 0
        if aggregate:
            names=list(aggregate)
            for values, title, color in [([aggregate[x][0] for x in names], "Расходы, у.е.", "#FFD400"),
                                         ([aggregate[x][1] for x in names], "Контакты", "#697386")]:
                fig=go.Figure(go.Bar(y=names,x=values,orientation="h",marker_color=color,
                                     text=[f"{v:,.0f}".replace(",", " ") for v in values],textposition="auto"))
                fig.update_layout(height=175,margin=dict(l=0,r=8,t=28,b=4),title=dict(text=title,font_size=13),
                                  paper_bgcolor="#0B0D10",plot_bgcolor="#0B0D10",font_color="#A6AFBD",
                                  showlegend=False,xaxis_gridcolor="#2A303A",yaxis_autorange="reversed")
                st.plotly_chart(fig,use_container_width=True,config={"displayModeBar":False})
            st.caption("Контакты показаны отдельно: бесплатный push остаётся видимым при нулевых расходах.")
        else: st.info("Нет данных для распределения бюджета.")
    if chosen:
        st.subheader("Выбранная кампания")
        ci=chosen["ci95"]; interval="Нет данных" if ci[0] is None or ci[1] is None else f"{ci[0]*100:.1f}% … {ci[1]*100:.1f}%"
        explanation=f"Выбран {chosen['channel'] or 'канал не указан'}. "
        if chosen["posterior_lift"] is not None: explanation += f"Оценка относительного эффекта: {chosen['posterior_lift']*100:.1f}%. "
        if ci[0] is not None: explanation += "Модельный интервал включает ноль." if ci[0] <= 0 <= ci[1] else "Модельный интервал не включает ноль."
        st.markdown(f'<div class="panel"><div class="eyebrow">{html.escape(chosen["name"])}</div><h3>{html.escape(chosen["current_tariff"] or "—")} → {html.escape(chosen["target_tariff"] or "—")}</h3><p class="fine">{html.escape(explanation)}</p><div class="detail-grid">'
                    f'<div class="datum"><span>Группа / охват</span><b>{fmt_num(chosen["group_size"])} / {fmt_num(chosen["contacts"])}</b></div><div class="datum"><span>Канал / расходы</span><b>{html.escape(chosen["channel"] or "—")} / {fmt_num(chosen["cost"], " у.е.")}</b></div>'
                    f'<div class="datum"><span>Ожидаемый эффект</span><b>{fmt_num(chosen["expected_net_effect"], " у.е.")}</b></div><div class="datum"><span>Posterior lift</span><b>{"Нет данных" if chosen["posterior_lift"] is None else f"{chosen["posterior_lift"]*100:.1f}%"}</b></div>'
                    f'<div class="datum"><span>Стандартное отклонение</span><b>{"Нет данных" if chosen["std"] is None else f"{chosen["std"]*100:.1f}%"}</b></div><div class="datum"><span>95% модельный интервал</span><b>{interval}</b></div>'
                    f'<div class="datum"><span>Risk-adjusted lift</span><b>{"Нет данных" if chosen["risk_adjusted_lift"] is None else f"{chosen["risk_adjusted_lift"]*100:.1f}%"}</b></div><div class="datum"><span>Пилоты / контакты</span><b>{fmt_num(chosen["pilots_used"])} / {fmt_num(chosen["pilot_customers"])}</b></div></div></div>',unsafe_allow_html=True)
        warnings=list(chosen["warnings"])
        if ci[0] is not None and ci[0] <= 0 <= ci[1]: warnings.append("Модельный интервал неопределённости включает ноль.")
        if chosen["expected_net_effect"] is not None and chosen["expected_net_effect"] < 0: warnings.append("Ожидаемый чистый эффект отрицательный.")
        for warning in warnings: st.warning(warning)
with tab_pilots:
    st.subheader("Pilot Intelligence")
    if report["pilots"]:
        st.caption(f"Выполнено пилотов: {len(report['pilots'])} · контактов: {sum(p['contacts'] or 0 for p in report['pilots']):,} · расходы: {sum(p['cost'] or 0 for p in report['pilots']):,.0f} у.е.")
        st.dataframe(pd.DataFrame(report["pilots"]), hide_index=True, use_container_width=True)
    else: st.info("В этом отчёте нет подробной истории пилотов.")

if not presentation:
    with st.expander("Экспорт"):
        st.caption("UI-экспорт не является официальным submission.csv. Официальная сдача формируется make_submission.py.")
        a,b,c=st.columns(3)
        a.download_button("CSV кампаний",export_csv(report),"campaign_plan.csv","text/csv",use_container_width=True)
        b.download_button("JSON отчёта",export_json(report),"dashboard_report.json","application/json",use_container_width=True)
        c.download_button("Markdown резюме",export_markdown(report),"campaign_summary.md","text/markdown",use_container_width=True)
