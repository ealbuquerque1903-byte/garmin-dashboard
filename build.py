#!/usr/bin/env python3
"""
Gera site estático a partir dos dados do Garmin.
Rode após sync.py para publicar no Netlify.
"""
import json
import shutil
import os
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

BASE   = Path(__file__).parent
DIST   = BASE / "dist"
DATA   = BASE / "garmin" / "history.json"

# ── setup ───────────────────────────────────────────────────────────────────

def load():
    if not DATA.exists():
        print("ERRO: garmin/history.json não encontrado. Rode sync.py primeiro.")
        raise SystemExit(1)
    return json.loads(DATA.read_text(encoding="utf-8"))

def setup_dist():
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir()
    # copy static assets
    static_src = BASE / "static"
    static_dst = DIST / "static"
    if static_src.exists():
        shutil.copytree(static_src, static_dst)

def env():
    return Environment(loader=FileSystemLoader(str(BASE / "templates")), autoescape=True)

# ── helpers ─────────────────────────────────────────────────────────────────

_WELL_KEYS = ("sleep_score","hrv_avg","rhr","body_battery_charged","stress_avg","training_readiness_score","steps")

def sorted_wellness(history):
    items = sorted(history.get("wellness", {}).items(), key=lambda x: x[0], reverse=True)
    # Remove dias completamente vazios (sem nenhum campo com valor real)
    return [(d, w) for d, w in items if any(w.get(k) for k in _WELL_KEYS)]

def sorted_activities(history):
    return sorted(history.get("activities", {}).values(),
                  key=lambda x: x.get("date", ""), reverse=True)

def clean_ts(lst):
    return json.dumps([v if v is not None else None for v in (lst or [])])

# ── pages ────────────────────────────────────────────────────────────────────

def build_index(jenv, history):
    wellness    = sorted_wellness(history)
    activities  = sorted_activities(history)
    recent_w    = list(reversed(wellness[:30]))

    # "hoje" = dia mais recente que tenha pelo menos um KPI real (wellness já filtrado)
    today_data = wellness[0][1] if wellness else {}

    chart_dates    = [w[0] for w in recent_w]
    chart_hrv      = [w[1].get("hrv_avg")                    for w in recent_w]
    chart_rhr      = [w[1].get("rhr")                        for w in recent_w]
    chart_sleep    = [w[1].get("sleep_score")                 for w in recent_w]
    chart_bb       = [w[1].get("body_battery_charged")        for w in recent_w]
    chart_readiness= [w[1].get("training_readiness_score")    for w in recent_w]

    act_sorted = sorted(activities, key=lambda x: x.get("date",""))[-30:]
    act_dates  = [a.get("date","")         for a in act_sorted]
    act_loads  = [a.get("training_load") or 0 for a in act_sorted]
    act_dists  = [a.get("distance_km") or 0   for a in act_sorted]

    tmpl = jenv.get_template("index.html")
    html = tmpl.render(
        activities    = activities,
        wellness      = wellness[:14],
        today         = today_data,
        chart_dates   = json.dumps(chart_dates),
        chart_hrv     = json.dumps(chart_hrv),
        chart_rhr     = json.dumps(chart_rhr),
        chart_sleep   = json.dumps(chart_sleep),
        chart_bb      = json.dumps(chart_bb),
        chart_readiness = json.dumps(chart_readiness),
        act_dates     = json.dumps(act_dates),
        act_loads     = json.dumps(act_loads),
        act_dists     = json.dumps(act_dists),
        static_prefix = "static",
    )
    (DIST / "index.html").write_text(html, encoding="utf-8")
    print("  ✓ index.html")

def build_activities(jenv, history):
    acts_dir = DIST / "activity"
    acts_dir.mkdir()
    devices_list    = history.get("devices", [])
    devices_map     = {d["id"]: d for d in devices_list}
    hr_zone_limits  = history.get("hr_zone_limits", {})
    for act_id, activity in history.get("activities", {}).items():
        act_date = activity.get("date", "")
        wellness = history.get("wellness", {}).get(act_date, {})
        ts = activity.get("timeseries", {})
        tmpl = jenv.get_template("activity.html")
        html = tmpl.render(
            activity         = activity,
            wellness         = wellness,
            ts_time          = clean_ts(ts.get("time")),
            ts_hr            = clean_ts(ts.get("hr")),
            ts_pace          = clean_ts(ts.get("pace")),
            ts_power         = clean_ts(ts.get("power")),
            ts_cadence       = clean_ts(ts.get("cadence")),
            ts_altitude      = clean_ts(ts.get("altitude")),
            ts_distance      = clean_ts(ts.get("distance")),
            ts_stamina       = clean_ts(ts.get("stamina")),
            ts_temperature   = clean_ts(ts.get("temperature")),
            ts_gct           = clean_ts(ts.get("gct")),
            ts_vo            = clean_ts(ts.get("vo")),
            has_power        = json.dumps(any(v for v in (ts.get("power") or []) if v)),
            devices_map      = devices_map,
            hr_zone_limits   = hr_zone_limits,
            static_prefix    = "../static",
            index_href       = "../index.html",
        )
        (acts_dir / f"{act_id}.html").write_text(html, encoding="utf-8")
        print(f"  ✓ activity/{act_id}.html  ({activity.get('name','?')} — {act_date})")

def build_wellness(jenv, history):
    well_dir = DIST / "wellness"
    well_dir.mkdir()
    for day, w in history.get("wellness", {}).items():
        tmpl = jenv.get_template("wellness.html")
        html = tmpl.render(wellness=w, day=day, static_prefix="../static", index_href="../index.html")
        (well_dir / f"{day}.html").write_text(html, encoding="utf-8")
    print(f"  ✓ {len(history.get('wellness',{}))} páginas de bem-estar")

# ── main ─────────────────────────────────────────────────────────────────────

def main():
    print("Carregando dados...")
    history = load()
    acts    = len(history.get("activities", {}))
    days    = len(history.get("wellness", {}))
    print(f"  {acts} atividade(s), {days} dia(s) de bem-estar\n")

    print("Gerando site estático em dist/...")
    setup_dist()
    jenv = env()
    build_index(jenv, history)
    build_activities(jenv, history)
    build_wellness(jenv, history)

    size = sum(f.stat().st_size for f in DIST.rglob("*") if f.is_file())
    print(f"\nSite gerado! {size//1024} KB em {DIST}")
    print("\nPróximos passos:")
    print("  1. Acesse https://app.netlify.com/drop")
    print("  2. Arraste a pasta 'dist/' para o navegador")
    print("  3. Pronto — URL pública em segundos!")

if __name__ == "__main__":
    main()
