#!/usr/bin/env python3
"""
Gera relatórios PDF a partir do garmin/history.json.

Uso:
  python3 report.py atividades    # PDF por atividade em garmin/new_activities.json
  python3 report.py consolidado   # PDF consolidado dos últimos 30 dias
  python3 report.py ambos         # os dois
"""

import json
import sys
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    Image, PageBreak, HRFlowable
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

# ── Caminhos ─────────────────────────────────────────────────────────────────

BASE        = Path(__file__).parent
HISTORY     = BASE / "garmin" / "history.json"
NEW_ACTS    = BASE / "garmin" / "new_activities.json"
REPORTS_DIR = BASE / "reports"

# ── Cores das zonas FC ───────────────────────────────────────────────────────

ZONE_COLORS     = ["#3b82f6", "#22c55e", "#eab308", "#f97316", "#ef4444"]
ZONE_COLORS_RGB = [(0.23, 0.51, 0.96), (0.13, 0.77, 0.37), (0.92, 0.70, 0.03),
                   (0.98, 0.45, 0.09), (0.94, 0.27, 0.27)]
ZONE_NAMES      = ["Z1 Recuperação", "Z2 Aeróbico", "Z3 Tempo", "Z4 Limiar", "Z5 VO2max"]

# ── Helpers ──────────────────────────────────────────────────────────────────

def val(v, fmt=None, suffix=""):
    """Formata valor ou retorna '—' se None."""
    if v is None:
        return "—"
    if fmt:
        return f"{fmt % v}{suffix}"
    return f"{v}{suffix}"

def load_history() -> dict:
    if not HISTORY.exists():
        print("ERRO: garmin/history.json não encontrado.")
        raise SystemExit(1)
    return json.loads(HISTORY.read_text(encoding="utf-8"))

def load_new_activity_ids() -> list:
    if not NEW_ACTS.exists():
        return []
    return json.loads(NEW_ACTS.read_text(encoding="utf-8"))

def mask_none(lst):
    """Substitui None por np.nan para uso em matplotlib."""
    return [np.nan if v is None else v for v in (lst or [])]

def time_to_min(seconds_list):
    """Converte lista de segundos em minutos para eixo X dos gráficos."""
    return [v / 60 if v is not None else np.nan for v in (seconds_list or [])]

# ── Estilos ──────────────────────────────────────────────────────────────────

def build_styles():
    base = getSampleStyleSheet()
    styles = {
        "title":    ParagraphStyle("title",    fontSize=18, fontName="Helvetica-Bold",
                                   spaceAfter=6, textColor=colors.HexColor("#0f1117")),
        "subtitle": ParagraphStyle("subtitle", fontSize=12, fontName="Helvetica-Bold",
                                   spaceAfter=4, textColor=colors.HexColor("#334155")),
        "section":  ParagraphStyle("section",  fontSize=10, fontName="Helvetica-Bold",
                                   spaceBefore=10, spaceAfter=4,
                                   textColor=colors.HexColor("#64748b"),
                                   borderPad=2),
        "body":     ParagraphStyle("body",     fontSize=9,  fontName="Helvetica",
                                   spaceAfter=2, textColor=colors.HexColor("#1e293b")),
        "small":    ParagraphStyle("small",    fontSize=8,  fontName="Helvetica",
                                   textColor=colors.HexColor("#64748b")),
        "center":   ParagraphStyle("center",   fontSize=9,  fontName="Helvetica",
                                   alignment=TA_CENTER),
    }
    return styles

# ── Gráficos matplotlib ───────────────────────────────────────────────────────

GRAPH_W = 16   # cm
GRAPH_H = 5    # cm

def fig_to_image(fig, width_cm=GRAPH_W, height_cm=GRAPH_H):
    """Salva figura matplotlib como PNG temporário e retorna flowable Image."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        fname = f.name
    fig.savefig(fname, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return Image(fname, width=width_cm * cm, height=height_cm * cm)

def chart_hr(time_secs, hr_vals, zone_limits):
    """Linha de FC × tempo com faixas de zonas."""
    t = time_to_min(time_secs)
    h = mask_none(hr_vals)
    fig, ax = plt.subplots(figsize=(GRAPH_W / 2.54, GRAPH_H / 2.54))

    # Faixas de zona (se disponíveis)
    if zone_limits and zone_limits.get("zones"):
        for i, z in enumerate(zone_limits["zones"]):
            fl = z.get("floor")
            cl = z.get("ceil")
            if fl is not None and cl is not None:
                ax.axhspan(fl, cl, alpha=0.08, color=ZONE_COLORS_RGB[i])

    ax.plot(t, h, color="#ef4444", linewidth=1.2, label="FC")
    ax.set_xlabel("Tempo (min)", fontsize=8)
    ax.set_ylabel("bpm", fontsize=8)
    ax.set_title("Frequência Cardíaca", fontsize=9, fontweight="bold")
    ax.tick_params(labelsize=7)
    ax.grid(True, alpha=0.3, linewidth=0.5)
    fig.tight_layout()
    return fig_to_image(fig)

def chart_pace(time_secs, pace_vals):
    """Pace × tempo (eixo Y invertido — menor é melhor)."""
    t = time_to_min(time_secs)
    p = mask_none(pace_vals)
    # Converter s/km para min/km para exibição
    p_min = [v / 60 if not np.isnan(v) else np.nan for v in p]
    fig, ax = plt.subplots(figsize=(GRAPH_W / 2.54, GRAPH_H / 2.54))
    ax.plot(t, p_min, color="#3b82f6", linewidth=1.2)
    ax.invert_yaxis()  # pace menor (mais rápido) fica em cima
    ax.set_xlabel("Tempo (min)", fontsize=8)
    ax.set_ylabel("min/km", fontsize=8)
    ax.set_title("Pace", fontsize=9, fontweight="bold")

    # Formatar eixo Y como mm:ss
    def fmt_pace_tick(val, pos):
        mins = int(val)
        secs = int((val - mins) * 60)
        return f"{mins}:{secs:02d}"
    ax.yaxis.set_major_formatter(plt.FuncFormatter(fmt_pace_tick))
    ax.tick_params(labelsize=7)
    ax.grid(True, alpha=0.3, linewidth=0.5)
    fig.tight_layout()
    return fig_to_image(fig)

def chart_altitude(time_secs, alt_vals):
    """Perfil de elevação (área preenchida)."""
    t = time_to_min(time_secs)
    a = mask_none(alt_vals)
    fig, ax = plt.subplots(figsize=(GRAPH_W / 2.54, GRAPH_H / 2.54))
    ax.fill_between(t, a, alpha=0.4, color="#22c55e")
    ax.plot(t, a, color="#22c55e", linewidth=1)
    ax.set_xlabel("Tempo (min)", fontsize=8)
    ax.set_ylabel("m", fontsize=8)
    ax.set_title("Altitude", fontsize=9, fontweight="bold")
    ax.tick_params(labelsize=7)
    ax.grid(True, alpha=0.3, linewidth=0.5)
    fig.tight_layout()
    return fig_to_image(fig)

def chart_generic(time_secs, vals, title, ylabel, color="#7c3aed"):
    t = time_to_min(time_secs)
    v = mask_none(vals)
    fig, ax = plt.subplots(figsize=(GRAPH_W / 2.54, GRAPH_H / 2.54))
    ax.plot(t, v, color=color, linewidth=1.2)
    ax.set_xlabel("Tempo (min)", fontsize=8)
    ax.set_ylabel(ylabel, fontsize=8)
    ax.set_title(title, fontsize=9, fontweight="bold")
    ax.tick_params(labelsize=7)
    ax.grid(True, alpha=0.3, linewidth=0.5)
    fig.tight_layout()
    return fig_to_image(fig)

def chart_zones_bar(zone_secs, zone_names, zone_colors_rgb):
    """Barras horizontais de tempo em cada zona FC."""
    if not any(zone_secs):
        return None
    mins = [s / 60 if s else 0 for s in zone_secs]
    fig, ax = plt.subplots(figsize=(GRAPH_W / 2.54, 3 / 2.54))
    bars = ax.barh(zone_names, mins, color=zone_colors_rgb, edgecolor="white", height=0.6)
    for bar, m in zip(bars, mins):
        if m > 0:
            ax.text(bar.get_width() + 0.1, bar.get_y() + bar.get_height() / 2,
                    f"{int(m)}min", va="center", fontsize=7)
    ax.set_xlabel("Minutos", fontsize=8)
    ax.set_title("Tempo em cada zona", fontsize=9, fontweight="bold")
    ax.tick_params(labelsize=7)
    ax.set_xlim(0, max(mins) * 1.2 if max(mins) > 0 else 1)
    fig.tight_layout()
    return fig_to_image(fig, width_cm=GRAPH_W, height_cm=3.5)

# ── Tabelas ───────────────────────────────────────────────────────────────────

TABLE_STYLE_BASE = TableStyle([
    ("BACKGROUND",   (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
    ("FONTNAME",     (0, 0), (-1, 0), "Helvetica-Bold"),
    ("FONTSIZE",     (0, 0), (-1, -1), 8),
    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
    ("GRID",         (0, 0), (-1, -1), 0.3, colors.HexColor("#e2e8f0")),
    ("TOPPADDING",   (0, 0), (-1, -1), 3),
    ("BOTTOMPADDING",(0, 0), (-1, -1), 3),
    ("LEFTPADDING",  (0, 0), (-1, -1), 5),
])

def kpi_table(rows, col_widths=None):
    """Tabela 2 colunas: Label | Valor, grupos de 2."""
    data = []
    flat = rows
    for i in range(0, len(flat), 2):
        row = [flat[i][0], flat[i][1]]
        if i + 1 < len(flat):
            row += [flat[i+1][0], flat[i+1][1]]
        else:
            row += ["", ""]
        data.append(row)
    widths = col_widths or [4*cm, 4*cm, 4*cm, 4*cm]
    t = Table(data, colWidths=widths)
    t.setStyle(TableStyle([
        ("FONTNAME",     (0, 0), (-1, -1), "Helvetica"),
        ("FONTNAME",     (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME",     (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE",     (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ("GRID",         (0, 0), (-1, -1), 0.3, colors.HexColor("#e2e8f0")),
        ("TOPPADDING",   (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 3),
        ("LEFTPADDING",  (0, 0), (-1, -1), 5),
    ]))
    return t

def splits_table(laps):
    """Tabela de splits por km."""
    headers = ["Km", "Distância", "Tempo", "Pace", "FC Méd", "FC Máx",
               "Elev↑", "Cadência", "Potência"]
    rows = [headers]
    for lap in laps:
        rows.append([
            str(lap.get("lap", "—")),
            f"{lap.get('distance_km', '—')} km",
            lap.get("duration") or "—",
            lap.get("pace") or "—",
            val(lap.get("avg_hr"), suffix=" bpm"),
            val(lap.get("max_hr"), suffix=" bpm"),
            val(lap.get("elevation_gain"), suffix=" m"),
            val(lap.get("avg_cadence"), suffix=" ppm"),
            val(lap.get("avg_power"), suffix=" W"),
        ])
    widths = [1*cm, 2*cm, 2*cm, 2*cm, 1.8*cm, 1.8*cm, 1.5*cm, 2*cm, 1.9*cm]
    t = Table(rows, colWidths=widths, repeatRows=1)
    t.setStyle(TABLE_STYLE_BASE)
    return t

def zones_table(zone_secs, zone_limits):
    """Tabela de zonas: nome, piso, teto, tempo, %."""
    total = sum(s or 0 for s in zone_secs)
    headers = ["Zona", "Piso (bpm)", "Teto (bpm)", "Tempo", "%"]
    rows = [headers]
    zlimits = (zone_limits or {}).get("zones", [])
    for i, s in enumerate(zone_secs):
        zl = zlimits[i] if i < len(zlimits) else {}
        mins = (s or 0) // 60
        secs = (s or 0) % 60
        pct  = f"{(s or 0) / total * 100:.0f}%" if total else "—"
        rows.append([
            ZONE_NAMES[i],
            val(zl.get("floor")),
            val(zl.get("ceil")),
            f"{mins}:{secs:02d} min",
            pct,
        ])
    widths = [4.5*cm, 2.5*cm, 2.5*cm, 3*cm, 2*cm]
    t = Table(rows, colWidths=widths, repeatRows=1)
    t.setStyle(TABLE_STYLE_BASE)
    return t

# ── PDF por atividade ─────────────────────────────────────────────────────────

def pdf_atividade(activity: dict, history: dict) -> Path:
    act_id   = activity.get("id", "?")
    act_date = activity.get("date", "sem-data")
    fname    = REPORTS_DIR / f"atividade_{act_date}_{act_id}.pdf"

    devices_map    = {d["id"]: d for d in history.get("devices", [])}
    hr_zone_limits = history.get("hr_zone_limits", {})
    wellness       = history.get("wellness", {}).get(act_date, {})
    ts             = activity.get("timeseries", {})
    laps           = activity.get("laps", [])
    styles         = build_styles()

    doc = SimpleDocTemplate(str(fname), pagesize=A4,
                            leftMargin=2*cm, rightMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    story = []

    # ── Cabeçalho ─────────────────────────────────────────────────────────
    act_type = activity.get("type", "")
    icon = ("🏃" if "running" in act_type else
            "🚴" if "cycling" in act_type or "biking" in act_type else
            "🏊" if "swimming" in act_type else "🏋️")
    story.append(Paragraph(f"{icon} {activity.get('name', 'Atividade')}", styles["title"]))

    dev = devices_map.get(activity.get("device_id", ""), {})
    meta_parts = [act_date]
    if activity.get("location"):
        meta_parts.append(activity["location"])
    if dev.get("name"):
        meta_parts.append(f"{dev['name']} (fw {dev.get('firmware','?')})")
    story.append(Paragraph(" · ".join(meta_parts), styles["small"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#e2e8f0"),
                             spaceAfter=8))

    # ── KPIs principais ────────────────────────────────────────────────────
    story.append(Paragraph("Resumo", styles["section"]))
    kpis = [
        ("Distância",     val(activity.get("distance_km"), suffix=" km")),
        ("Duração total", activity.get("duration") or "—"),
        ("Em movimento",  activity.get("moving_duration") or "—"),
        ("Parado",        activity.get("stopped_duration") or "—"),
        ("Pace médio",    activity.get("pace") or "—"),
        ("Melhor 1k",     activity.get("fastest_1k") or "—"),
        ("FC média",      val(activity.get("avg_hr"), suffix=" bpm")),
        ("FC máxima",     val(activity.get("max_hr"), suffix=" bpm")),
        ("Calorias",      val(activity.get("calories"), suffix=" kcal")),
        ("Cadência",      val(activity.get("cadence"), "%.0f", " ppm")),
        ("Elevação↑",     val(activity.get("elevation_gain"), suffix=" m")),
        ("Elevação↓",     val(activity.get("elevation_loss"), suffix=" m")),
        ("Carga treino",  val(activity.get("training_load"), "%.0f")),
        ("VO2max",        val(activity.get("vo2max"))),
        ("TE Aeróbico",   val(activity.get("aerobic_te"), "%.1f") +
                          (f" ({activity['te_label']})" if activity.get("te_label") else "")),
        ("TE Anaeróbico", val(activity.get("anaerobic_te"), "%.1f")),
        ("RPE",           activity.get("rpe_label") or "—"),
        ("Como se sentiu",activity.get("feel_label") or "—"),
        ("Stamina início",val(activity.get("stamina_start"), "%.0f", "%")),
        ("Stamina final", val(activity.get("stamina_end"), "%.0f", "%")),
        ("Stamina mín",   val(activity.get("stamina_min"), "%.0f", "%")),
        ("FC recuperação",val(activity.get("recovery_hr"), suffix=" bpm")),
        ("Impact load",   val(activity.get("impact_load"))),
        ("Battery drenada",val(activity.get("body_battery_drain"))),
    ]
    if activity.get("avg_power"):
        kpis += [
            ("Potência média", val(activity.get("avg_power"), "%.0f", " W")),
            ("Potência norm.", val(activity.get("normalized_power"), "%.0f", " W")),
        ]
    story.append(kpi_table(kpis))
    story.append(Spacer(1, 6))

    # ── Clima ──────────────────────────────────────────────────────────────
    if any(activity.get(k) for k in ("temp_c","humidity","wind_speed","weather_desc")):
        story.append(Paragraph("Clima", styles["section"]))
        clima = [
            ("Temperatura",    val(activity.get("temp_c"), "%.1f", "°C")),
            ("Sensação",       val(activity.get("apparent_temp_c"), "%.1f", "°C")),
            ("Umidade",        val(activity.get("humidity"), suffix="%")),
            ("Ponto de orvalho",val(activity.get("dew_point_c"), "%.1f", "°C")),
            ("Vento",          f"{val(activity.get('wind_speed'))} km/h {activity.get('wind_dir','') or ''}".strip()),
            ("Condição",       activity.get("weather_desc") or "—"),
        ]
        story.append(kpi_table(clima))
        story.append(Spacer(1, 6))

    # ── Gráficos de timeseries ─────────────────────────────────────────────
    t_secs = ts.get("time") or []
    hr_vals = ts.get("hr") or []
    pa_vals = ts.get("pace") or []
    al_vals = ts.get("altitude") or []
    pw_vals = ts.get("power") or []
    cd_vals = ts.get("cadence") or []
    st_vals = ts.get("stamina") or []

    has_hr      = any(v is not None for v in hr_vals)
    has_pace    = any(v is not None for v in pa_vals)
    has_alt     = any(v is not None for v in al_vals)
    has_power   = any(v is not None for v in pw_vals)
    has_cadence = any(v is not None for v in cd_vals)
    has_stamina = any(v is not None for v in st_vals)

    if any([has_hr, has_pace, has_alt]):
        story.append(Paragraph("Gráficos", styles["section"]))
        if has_hr:
            story.append(chart_hr(t_secs, hr_vals, hr_zone_limits))
            story.append(Spacer(1, 4))
        if has_pace:
            story.append(chart_pace(t_secs, pa_vals))
            story.append(Spacer(1, 4))
        if has_alt:
            story.append(chart_altitude(t_secs, al_vals))
            story.append(Spacer(1, 4))
        if has_power:
            story.append(chart_generic(t_secs, pw_vals, "Potência", "W", "#f97316"))
            story.append(Spacer(1, 4))
        if has_cadence:
            story.append(chart_generic(t_secs, cd_vals, "Cadência", "ppm", "#7c3aed"))
            story.append(Spacer(1, 4))
        if has_stamina:
            story.append(chart_generic(t_secs, st_vals, "Stamina", "%", "#22c55e"))
            story.append(Spacer(1, 4))

    # ── Zonas FC ───────────────────────────────────────────────────────────
    zone_secs = activity.get("hr_zone_times") or []
    if not any(zone_secs) and activity.get("hr_zones"):
        zone_secs = [z.get("secsInZone") for z in activity["hr_zones"]]

    if any(zone_secs):
        story.append(Paragraph("Zonas de Frequência Cardíaca", styles["section"]))
        bar = chart_zones_bar(zone_secs, ZONE_NAMES, ZONE_COLORS_RGB)
        if bar:
            story.append(bar)
            story.append(Spacer(1, 4))
        story.append(zones_table(zone_secs, hr_zone_limits))
        story.append(Spacer(1, 6))

    # ── Splits ─────────────────────────────────────────────────────────────
    if laps:
        story.append(Paragraph(f"Splits ({len(laps)} km)", styles["section"]))
        story.append(splits_table(laps))
        story.append(Spacer(1, 6))

    # ── Wellness do dia ────────────────────────────────────────────────────
    if any(wellness.get(k) for k in ("sleep_score","hrv_avg","rhr","training_readiness_score")):
        story.append(Paragraph("Bem-Estar do Dia", styles["section"]))
        w_kpis = [
            ("Sono",        val(wellness.get("sleep_score"), suffix="/100") +
                            (f" ({wellness['sleep_duration']})" if wellness.get("sleep_duration") else "")),
            ("HRV",         val(wellness.get("hrv_avg"), "%.0f", " ms") +
                            (f" ({wellness['hrv_status']})" if wellness.get("hrv_status") else "")),
            ("FC repouso",  val(wellness.get("rhr"), suffix=" bpm")),
            ("Prontidão",   val(wellness.get("training_readiness_score"), suffix="/100") +
                            (f" ({wellness['training_readiness_level']})" if wellness.get("training_readiness_level") else "")),
            ("Body battery",val(wellness.get("body_battery_charged"), suffix="%")),
            ("Estresse",    val(wellness.get("stress_avg"), suffix="/100")),
        ]
        story.append(kpi_table(w_kpis))

    doc.build(story)
    print(f"  ✓ {fname.name}")
    return fname

# ── PDF consolidado ───────────────────────────────────────────────────────────

def pdf_consolidado(history: dict) -> Path:
    today     = date.today()
    start     = today - timedelta(days=30)
    start_str = start.isoformat()
    fname     = REPORTS_DIR / f"consolidado_{start_str}_{today.isoformat()}.pdf"

    styles = build_styles()

    # Atividades do período
    activities = sorted(
        [a for a in history.get("activities", {}).values()
         if (a.get("date") or "") >= start_str],
        key=lambda x: x.get("date", "")
    )

    # Wellness filtrado (só dias com dados reais)
    wellness_items = sorted(
        [(d, w) for d, w in history.get("wellness", {}).items()
         if d >= start_str and any(w.get(k) for k in
            ("sleep_score","hrv_avg","rhr","training_readiness_score","body_battery_charged"))],
        key=lambda x: x[0]
    )

    doc = SimpleDocTemplate(str(fname), pagesize=A4,
                            leftMargin=2*cm, rightMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    story = []

    # ── Cabeçalho ─────────────────────────────────────────────────────────
    story.append(Paragraph("📊 Relatório Consolidado", styles["title"]))
    story.append(Paragraph(f"{start_str} → {today.isoformat()}", styles["small"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#e2e8f0"),
                             spaceAfter=8))

    # ── Sumário ────────────────────────────────────────────────────────────
    total_dist  = sum(a.get("distance_km") or 0 for a in activities)
    total_secs  = sum(a.get("duration_secs") or 0 for a in activities)
    total_load  = sum(a.get("training_load") or 0 for a in activities)
    total_kcal  = sum(a.get("calories") or 0 for a in activities)
    total_h     = int(total_secs // 3600)
    total_m     = int((total_secs % 3600) // 60)

    story.append(Paragraph("Sumário do Período", styles["section"]))
    sumario = [
        ("Atividades",       str(len(activities))),
        ("Distância total",  f"{total_dist:.1f} km"),
        ("Tempo total",      f"{total_h}h {total_m:02d}min"),
        ("Carga acumulada",  f"{total_load:.0f}"),
        ("Calorias totais",  f"{total_kcal:.0f} kcal"),
        ("Dias de wellness", str(len(wellness_items))),
    ]
    story.append(kpi_table(sumario))
    story.append(Spacer(1, 8))

    # ── Gráficos de tendência ──────────────────────────────────────────────
    if activities:
        story.append(Paragraph("Tendências", styles["section"]))

        # Carga de treino + distância por atividade
        act_dates  = [a.get("date", "") for a in activities]
        act_loads  = [a.get("training_load") or 0 for a in activities]
        act_dists  = [a.get("distance_km") or 0 for a in activities]
        act_labels = [d[-5:] for d in act_dates]  # MM-DD

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(GRAPH_W / 2.54, 8 / 2.54), sharex=True)
        x = range(len(activities))
        ax1.bar(x, act_loads, color="#f97316", alpha=0.8, label="Carga")
        ax1.set_ylabel("Carga", fontsize=8)
        ax1.set_title("Carga de Treino", fontsize=9, fontweight="bold")
        ax1.tick_params(labelsize=7)
        ax1.grid(True, alpha=0.3, axis="y")

        ax2.bar(x, act_dists, color="#3b82f6", alpha=0.8, label="Distância")
        ax2.set_ylabel("km", fontsize=8)
        ax2.set_title("Distância", fontsize=9, fontweight="bold")
        ax2.set_xticks(list(x))
        ax2.set_xticklabels(act_labels, rotation=45, ha="right", fontsize=6)
        ax2.grid(True, alpha=0.3, axis="y")
        fig.tight_layout()
        story.append(fig_to_image(fig, width_cm=GRAPH_W, height_cm=8))
        story.append(Spacer(1, 6))

        # VO2max ao longo do tempo
        vo2_acts = [(a.get("date",""), a.get("vo2max")) for a in activities if a.get("vo2max")]
        if vo2_acts:
            vo2_d = [d for d, _ in vo2_acts]
            vo2_v = [v for _, v in vo2_acts]
            fig, ax = plt.subplots(figsize=(GRAPH_W / 2.54, GRAPH_H / 2.54))
            ax.plot(vo2_d, vo2_v, "o-", color="#7c3aed", linewidth=1.5, markersize=4)
            ax.set_ylabel("VO2max", fontsize=8)
            ax.set_title("VO2max", fontsize=9, fontweight="bold")
            ax.tick_params(axis="x", rotation=45, labelsize=6)
            ax.tick_params(axis="y", labelsize=7)
            ax.grid(True, alpha=0.3)
            fig.tight_layout()
            story.append(fig_to_image(fig))
            story.append(Spacer(1, 6))

    # ── Wellness trends ────────────────────────────────────────────────────
    if wellness_items:
        w_dates  = [d for d, _ in wellness_items]
        w_labels = [d[-5:] for d in w_dates]

        def w_series(key):
            return [w.get(key) for _, w in wellness_items]

        hrv_v   = mask_none(w_series("hrv_avg"))
        rhr_v   = mask_none(w_series("rhr"))
        sleep_v = mask_none(w_series("sleep_score"))
        ready_v = mask_none(w_series("training_readiness_score"))
        bb_v    = mask_none(w_series("body_battery_charged"))

        fig, axes = plt.subplots(3, 1, figsize=(GRAPH_W / 2.54, 12 / 2.54), sharex=True)
        x = range(len(w_dates))

        axes[0].plot(list(x), hrv_v, "o-", color="#00d4ff", linewidth=1.2, markersize=3, label="HRV (ms)")
        ax0b = axes[0].twinx()
        ax0b.plot(list(x), rhr_v, "s--", color="#ef4444", linewidth=1, markersize=3, label="FC rep (bpm)")
        axes[0].set_title("HRV & FC Repouso", fontsize=9, fontweight="bold")
        axes[0].set_ylabel("HRV (ms)", fontsize=7, color="#00d4ff")
        ax0b.set_ylabel("FC (bpm)", fontsize=7, color="#ef4444")
        axes[0].tick_params(labelsize=6)
        ax0b.tick_params(labelsize=6)
        axes[0].grid(True, alpha=0.3)

        axes[1].plot(list(x), sleep_v, "o-", color="#7c3aed", linewidth=1.2, markersize=3, label="Sono")
        axes[1].plot(list(x), ready_v, "s--", color="#22c55e", linewidth=1, markersize=3, label="Prontidão")
        axes[1].set_title("Sono & Prontidão (/100)", fontsize=9, fontweight="bold")
        axes[1].set_ylim(0, 100)
        axes[1].tick_params(labelsize=6)
        axes[1].grid(True, alpha=0.3)
        axes[1].legend(fontsize=6, loc="lower left")

        axes[2].bar(list(x), bb_v, color="#22c55e", alpha=0.8)
        axes[2].set_title("Body Battery (%)", fontsize=9, fontweight="bold")
        axes[2].set_ylim(0, 100)
        axes[2].set_xticks(list(x))
        axes[2].set_xticklabels(w_labels, rotation=45, ha="right", fontsize=6)
        axes[2].tick_params(labelsize=6)
        axes[2].grid(True, alpha=0.3, axis="y")

        fig.tight_layout()
        story.append(Paragraph("Bem-Estar — Tendência 30 Dias", styles["section"]))
        story.append(fig_to_image(fig, width_cm=GRAPH_W, height_cm=12))
        story.append(Spacer(1, 6))

    # ── Tabela de atividades ───────────────────────────────────────────────
    if activities:
        story.append(PageBreak())
        story.append(Paragraph("Todas as Atividades do Período", styles["section"]))
        headers = ["Data", "Nome", "Tipo", "Dist.", "Duração", "Pace", "FC", "Carga"]
        rows = [headers]
        for a in reversed(activities):
            act_type = a.get("type","")
            tipo_label = ("Corrida" if "running" in act_type else
                          "Ciclismo" if "cycling" in act_type else
                          "Força" if "strength" in act_type else
                          act_type.replace("_"," ").title())
            rows.append([
                a.get("date",""),
                (a.get("name","") or "")[:28],
                tipo_label,
                val(a.get("distance_km"), "%.1f", " km"),
                a.get("duration") or "—",
                a.get("pace") or "—",
                val(a.get("avg_hr"), suffix=" bpm"),
                val(a.get("training_load"), "%.0f"),
            ])
        widths = [2*cm, 5*cm, 2.2*cm, 1.8*cm, 2.2*cm, 2*cm, 1.8*cm, 1.5*cm]
        t = Table(rows, colWidths=widths, repeatRows=1)
        t.setStyle(TABLE_STYLE_BASE)
        story.append(t)

    doc.build(story)
    print(f"  ✓ {fname.name}")
    return fname

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("atividades", "consolidado", "ambos"):
        print("Uso: python3 report.py [atividades|consolidado|ambos]")
        raise SystemExit(1)

    mode = sys.argv[1]
    REPORTS_DIR.mkdir(exist_ok=True)
    history = load_history()

    if mode in ("atividades", "ambos"):
        new_ids = load_new_activity_ids()
        print(f"Gerando PDFs de atividades ({len(new_ids)} nova(s))...")
        for act_id in new_ids:
            act = history.get("activities", {}).get(act_id)
            if not act:
                print(f"  ⚠ atividade {act_id} não encontrada em history.json")
                continue
            pdf_atividade(act, history)

    if mode in ("consolidado", "ambos"):
        print("Gerando PDF consolidado...")
        pdf_consolidado(history)

    print(f"\nRelatórios salvos em {REPORTS_DIR}/")

if __name__ == "__main__":
    main()
