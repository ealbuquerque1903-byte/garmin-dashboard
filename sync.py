#!/usr/bin/env python3
"""
Garmin Sync — coleta treinos + bem-estar do Garmin Connect,
gera análises com IA (Claude) e salva tudo em garmin/
"""

import json
import os
import sys
import warnings
warnings.filterwarnings("ignore")

# Load .env if present
_env_file = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_env_file):
    for _line in open(_env_file):
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

from datetime import date, timedelta
from pathlib import Path

TOKEN_DIR  = str(Path(__file__).parent / ".garmin_tokens")
GARMIN_DIR = Path(__file__).parent / "garmin"
DB_FILE    = Path(__file__).parent / "garmin" / "history.json"

# ── client ─────────────────────────────────────────────────────────────────

def load_client():
    from garminconnect import Garmin
    if Path(TOKEN_DIR).exists():
        client = Garmin()
        try:
            client.login(tokenstore=TOKEN_DIR)
            return client
        except Exception:
            pass
    print("Token não encontrado. Por favor rode primeiro: python3 login.py")
    raise SystemExit(1)

# ── formatters ──────────────────────────────────────────────────────────────

def fmt_duration(seconds):
    if not seconds:
        return "—"
    s = int(seconds)
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}h {m:02d}min" if h else f"{m}min {sec:02d}s"

def fmt_pace(mps):
    if not mps:
        return "—"
    spk = 1000 / float(mps)
    return f"{int(spk)//60}:{int(spk)%60:02d} /km"

def fmt_dist(meters):
    if not meters:
        return "—"
    return f"{float(meters)/1000:.2f} km"

# ── timeseries extraction ──────────────────────────────────────────────────

def extract_timeseries(details: dict) -> dict:
    """
    Extrai séries temporais usando metricDescriptors para mapear índices → nomes.
    """
    series = {"time": [], "hr": [], "pace": [], "power": [],
              "cadence": [], "altitude": [], "distance": []}

    descriptors = details.get("metricDescriptors") or []
    metrics_pts  = details.get("activityDetailMetrics") or []
    if not descriptors or not metrics_pts:
        return series

    # build index map
    idx = {d["key"]: i for i, d in enumerate(descriptors)}

    def get_val(row, *keys):
        for k in keys:
            i = idx.get(k)
            if i is not None and i < len(row):
                v = row[i]
                if v is not None:
                    return v
        return None

    for point in metrics_pts:
        row = point.get("metrics") or []

        elapsed = get_val(row, "sumElapsedDuration", "sumDuration")
        series["time"].append(round(elapsed, 1) if elapsed is not None else None)

        hr = get_val(row, "directHeartRate")
        series["hr"].append(round(hr) if hr is not None else None)

        speed = get_val(row, "directSpeed")
        if speed and float(speed) > 0.1:
            series["pace"].append(round(1000 / float(speed), 1))
        else:
            series["pace"].append(None)

        power = get_val(row, "directPower", "sumAccumulatedPower")
        series["power"].append(round(power, 1) if power is not None else None)

        cadence = get_val(row, "directRunCadence", "directFractionalCadence", "directDoubleCadence")
        series["cadence"].append(round(cadence, 1) if cadence is not None else None)

        alt = get_val(row, "directElevation")
        series["altitude"].append(round(alt, 1) if alt is not None else None)

        dist = get_val(row, "sumDistance")
        series["distance"].append(round(dist, 1) if dist is not None else None)

    return series


def extract_laps(splits_data: dict) -> list:
    splits = (splits_data.get("splitSummaries") or []) if isinstance(splits_data, dict) else []
    result = []
    for i, lap in enumerate(splits):
        result.append({
            "lap":           i + 1,
            "distance_km":   round(float(lap.get("distance", 0)) / 1000, 2),
            "duration":      fmt_duration(lap.get("duration")),
            "pace":          fmt_pace(lap.get("averageSpeed")),
            "avg_hr":        lap.get("averageHR"),
            "max_hr":        lap.get("maxHR"),
            "calories":      lap.get("calories"),
            "elevation_gain": lap.get("elevationGain"),
            "avg_power":     lap.get("averagePower"),
            "normalized_power": lap.get("normalizedPower"),
            "avg_cadence":   lap.get("averageRunCadence") or lap.get("averageBikingCadenceInRevPerMinute"),
        })
    return result

# ── AI analysis ────────────────────────────────────────────────────────────

def generate_ai_analysis(activity_summary: dict, wellness: dict) -> str:
    try:
        import anthropic
        client = anthropic.Anthropic()

        act_type = activity_summary.get("type", "atividade")
        dist     = activity_summary.get("distance_km", "—")
        duration = activity_summary.get("duration", "—")
        pace     = activity_summary.get("pace", "—")
        avg_hr   = activity_summary.get("avg_hr", "—")
        max_hr   = activity_summary.get("max_hr", "—")
        calories = activity_summary.get("calories", "—")
        load     = activity_summary.get("training_load", "—")
        vo2      = activity_summary.get("vo2max", "—")
        elev     = activity_summary.get("elevation_gain", "—")
        cadence  = activity_summary.get("cadence", "—")
        laps     = activity_summary.get("laps", [])

        hrv      = wellness.get("hrv_avg", "—")
        rhr      = wellness.get("rhr", "—")
        sleep_sc = wellness.get("sleep_score", "—")
        bb       = wellness.get("body_battery_charged", "—")
        stress   = wellness.get("stress_avg", "—")
        tr_score = wellness.get("training_readiness_score", "—")

        laps_text = ""
        if laps:
            laps_text = "\nVoltas:\n" + "\n".join(
                f"  Volta {l['lap']}: {l['distance_km']}km em {l['duration']}, "
                f"pace {l['pace']}, FC avg {l['avg_hr']} bpm"
                for l in laps[:10]
            )

        prompt = f"""Você é um treinador de corrida e ciclismo experiente e analítico.
Analise este treino e forneça feedback construtivo e detalhado em português brasileiro.

DADOS DO TREINO:
- Tipo: {act_type}
- Distância: {dist}
- Duração: {duration}
- Pace médio: {pace}
- FC média: {avg_hr} bpm | FC máxima: {max_hr} bpm
- Calorias: {calories} kcal
- Carga de treino: {load}
- VO2max estimado: {vo2}
- Ganho de elevação: {elev} m
- Cadência média: {cadence} ppm
{laps_text}

RECUPERAÇÃO NO DIA DO TREINO:
- HRV noturna: {hrv} ms
- FC em repouso: {rhr} bpm
- Pontuação do sono: {sleep_sc}/100
- Body Battery: {bb}%
- Estresse médio: {stress}/100
- Prontidão para treino: {tr_score}/100

Forneça uma análise com as seguintes seções (use markdown com ##):

## ✅ Pontos Positivos
O que foi bem neste treino.

## ⚠️ Atenção
Alertas ou pontos de melhoria observados nos dados.

## 📈 Análise de Performance
Análise detalhada do ritmo, frequência cardíaca, zonas de esforço e consistência entre as voltas.

## 🔄 Recuperação e Prontidão
Como os dados de recuperação influenciaram este treino. O atleta estava bem recuperado?

## 💡 Recomendações para o Próximo Treino
Sugestões específicas e práticas baseadas nos dados de hoje.

## 📊 Indicadores Chave
- Eficiência cardiovascular (pace vs FC)
- Carga de treino: adequada / leve / excessiva
- Nível de fadiga estimado

Seja direto, use dados concretos e evite generalidades. Máximo 400 palavras."""

        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}]
        )
        return message.content[0].text

    except Exception as e:
        return f"*Análise de IA não disponível: {e}*"


def generate_wellness_ai(wellness: dict) -> str:
    try:
        import anthropic
        client = anthropic.Anthropic()

        prompt = f"""Você é um especialista em recuperação esportiva e saúde do sono.
Analise estes dados de recuperação e dê orientações em português brasileiro.

DADOS DO DIA:
- HRV noturna: {wellness.get('hrv_avg', '—')} ms
- FC em repouso: {wellness.get('rhr', '—')} bpm
- Pontuação do sono: {wellness.get('sleep_score', '—')}/100
- Duração do sono: {wellness.get('sleep_duration', '—')}
- Deep sleep: {wellness.get('deep_sleep', '—')}
- REM sleep: {wellness.get('rem_sleep', '—')}
- Body Battery carregado: {wellness.get('body_battery_charged', '—')}%
- Body Battery drenado: {wellness.get('body_battery_drained', '—')}%
- Estresse médio: {wellness.get('stress_avg', '—')}/100
- Prontidão para treino: {wellness.get('training_readiness_score', '—')}/100
- Nível de prontidão: {wellness.get('training_readiness_level', '—')}
- Passos: {wellness.get('steps', '—')}

Forneça uma análise curta (máximo 200 palavras) com:
## 💤 Status de Recuperação
## 🎯 Recomendação do Dia

Seja direto e prático."""

        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}]
        )
        return message.content[0].text
    except Exception as e:
        return f"*Análise não disponível: {e}*"

# ── wellness ───────────────────────────────────────────────────────────────

def fetch_wellness(client, day: date) -> dict:
    ds = day.isoformat()
    raw = {}

    for key, fn in [
        ("sleep",               lambda: client.get_sleep_data(ds)),
        ("hrv",                 lambda: client.get_hrv_data(ds)),
        ("heart_rate",          lambda: client.get_rhr_day(ds)),
        ("body_battery",        lambda: client.get_body_battery(ds)),
        ("stress",              lambda: client.get_stress_data(ds)),
        ("steps",               lambda: client.get_steps_data(ds)),
        ("training_readiness",  lambda: client.get_training_readiness(ds)),
    ]:
        try:
            raw[key] = fn()
        except Exception:
            raw[key] = {} if key in ("sleep","hrv","heart_rate","stress") else []

    sleep_dto  = (raw["sleep"].get("dailySleepDTO") or {}) if isinstance(raw["sleep"], dict) else {}
    hrv_sum    = (raw["hrv"].get("hrvSummary") or {})       if isinstance(raw["hrv"], dict)   else {}
    body_list  = raw["body_battery"] if isinstance(raw["body_battery"], list) else []
    tr_list    = raw["training_readiness"] if isinstance(raw["training_readiness"], list) else []
    steps_list = raw["steps"] if isinstance(raw["steps"], list) else []

    sleep_secs = sleep_dto.get("sleepTimeSeconds")
    deep_secs  = sleep_dto.get("deepSleepSeconds")
    rem_secs   = sleep_dto.get("remSleepSeconds")

    def secs_to_str(s):
        if not s:
            return None
        return f"{int(s)//3600}h {(int(s)%3600)//60:02d}min"

    rhr = None
    try:
        m = raw["heart_rate"].get("allMetrics", {}).get("metricsMap", {})
        rhr_list = m.get("WELLNESS_RESTING_HEART_RATE", [])
        if rhr_list:
            rhr = int(rhr_list[0]["value"])
    except Exception:
        pass

    return {
        "date": ds,
        "sleep_score":            (sleep_dto.get("sleepScores") or {}).get("overall", {}).get("value"),
        "sleep_duration":         secs_to_str(sleep_secs),
        "sleep_seconds":          sleep_secs,
        "deep_sleep":             secs_to_str(deep_secs),
        "rem_sleep":              secs_to_str(rem_secs),
        "hrv_avg":                raw["sleep"].get("avgOvernightHrv") or hrv_sum.get("lastNightAvg") if isinstance(raw["sleep"], dict) else hrv_sum.get("lastNightAvg"),
        "hrv_status":             hrv_sum.get("status"),
        "rhr":                    rhr,
        "body_battery_charged":   body_list[0].get("charged") if body_list else None,
        "body_battery_drained":   body_list[0].get("drained") if body_list else None,
        "stress_avg":             raw["stress"].get("avgStressLevel") if isinstance(raw["stress"], dict) else None,
        "training_readiness_score": tr_list[0].get("score") if tr_list else None,
        "training_readiness_level": tr_list[0].get("level") if tr_list else None,
        "steps":                  sum(s.get("steps", 0) for s in steps_list),
        "_raw":                   raw,
    }

# ── activity ───────────────────────────────────────────────────────────────

def fetch_activity(client, act: dict, wellness_today: dict) -> dict:
    act_id   = act.get("activityId")
    act_date = (act.get("startTimeLocal") or "")[:10]

    print(f"    → Buscando detalhes e timeseries de {act.get('activityName','?')}...")

    details    = {}
    splits_data = {}
    hr_zones   = {}

    try:
        details = client.get_activity_details(act_id, maxchart=2000)
    except Exception as e:
        print(f"      Aviso detalhes: {e}")
    try:
        splits_data = client.get_activity_split_summaries(act_id)
    except Exception as e:
        print(f"      Aviso splits: {e}")
    try:
        hr_zones = client.get_activity_hr_in_timezones(act_id)
    except Exception:
        pass

    timeseries = extract_timeseries(details)
    laps       = extract_laps(splits_data)

    dist_m  = act.get("distance")
    speed   = act.get("averageSpeed")

    summary = {
        "id":            act_id,
        "date":          act_date,
        "name":          act.get("activityName", "Atividade"),
        "type":          act.get("activityType", {}).get("typeKey", "unknown"),
        "distance_km":   round(float(dist_m) / 1000, 2) if dist_m else None,
        "duration":      fmt_duration(act.get("duration")),
        "duration_secs": act.get("duration"),
        "calories":      act.get("calories"),
        "pace":          fmt_pace(speed),
        "avg_speed_mps": speed,
        "avg_hr":        act.get("averageHR"),
        "max_hr":        act.get("maxHR"),
        "elevation_gain": act.get("elevationGain"),
        "cadence":       act.get("averageRunningCadenceInStepsPerMinute") or act.get("averageBikingCadenceInRevPerMinute"),
        "training_load":    act.get("activityTrainingLoad"),
        "vo2max":           act.get("vO2MaxValue"),
        "avg_power":        splits_data.get("splitSummaries", [{}])[0].get("averagePower") if splits_data.get("splitSummaries") else None,
        "normalized_power": splits_data.get("splitSummaries", [{}])[0].get("normalizedPower") if splits_data.get("splitSummaries") else None,
        "hr_zones":         hr_zones,
        "timeseries":       timeseries,
        "laps":             laps,
    }

    print(f"    → Gerando análise de IA...")
    summary["ai_analysis"] = generate_ai_analysis(summary, wellness_today)

    return summary

# ── history ────────────────────────────────────────────────────────────────

def load_history() -> dict:
    if DB_FILE.exists():
        try:
            return json.loads(DB_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"wellness": {}, "activities": {}}

def save_history(history: dict):
    DB_FILE.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")

# ── main sync ──────────────────────────────────────────────────────────────

def sync(days: int = 7):
    client = load_client()
    name   = client.get_full_name()
    print(f"Conectado como: {name}\n")

    GARMIN_DIR.mkdir(exist_ok=True)
    history = load_history()
    today   = date.today()

    # ── wellness ──
    print(f"Sincronizando bem-estar ({days} dias)...")
    for i in range(days):
        day = today - timedelta(days=i)
        ds  = day.isoformat()
        print(f"  {ds}...", end=" ", flush=True)
        w = fetch_wellness(client, day)
        w["ai_analysis"] = generate_wellness_ai(w)
        history["wellness"][ds] = w
        print("✓")

    # ── activities ──
    start_str = (today - timedelta(days=days)).isoformat()
    print(f"\nSincronizando atividades ({days} dias)...")

    try:
        activities = client.get_activities_by_date(start_str, today.isoformat())
    except Exception as e:
        print(f"  Aviso: {e}")
        activities = []

    for act in activities:
        act_id = str(act.get("activityId", ""))
        act_name = act.get("activityName", "?")
        act_date = (act.get("startTimeLocal") or "")[:10]

        if act_id in history["activities"]:
            print(f"  {act_date} — {act_name} [já sincronizado, pulando]")
            continue

        print(f"  {act_date} — {act_name}")
        wellness_day = history["wellness"].get(act_date, {})
        full = fetch_activity(client, act, wellness_day)
        history["activities"][act_id] = full
        print(f"    ✓ salvo")

    save_history(history)
    acts_count = len(activities)
    print(f"\nSincronização concluída! {days} dias de bem-estar, {acts_count} atividade(s).")
    print(f"Dados em: {DB_FILE}")

def git_push():
    """Faz commit dos dados atualizados e push para o GitHub."""
    import subprocess
    repo = Path(__file__).parent

    def run(cmd):
        return subprocess.run(cmd, cwd=repo, capture_output=True, text=True)

    # Verifica se é um repo git
    if not (repo / ".git").exists():
        print("\nAviso: pasta não é um repo git. Rode 'git init' para ativar auto-publish.")
        return

    # Verifica se tem remote configurado
    r = run(["git", "remote", "get-url", "origin"])
    if r.returncode != 0:
        print("\nAviso: nenhum remote 'origin' configurado. Dados salvos localmente.")
        return

    today = date.today().isoformat()
    run(["git", "add", "dist/", "garmin/history.json"])
    r = run(["git", "commit", "-m", f"sync: {today}"])
    if "nothing to commit" in r.stdout:
        print("Git: nada novo para publicar.")
        return
    r = run(["git", "push", "origin", "main"])
    if r.returncode == 0:
        print("✓ Publicado no GitHub → Netlify atualizará em ~30 segundos.")
    else:
        print(f"Aviso no push: {r.stderr[:200]}")


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    sync(days)

    # Rebuild site estático
    print("\nGerando site estático...")
    import importlib.util, types
    spec = importlib.util.spec_from_file_location("build", Path(__file__).parent / "build.py")
    build_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build_mod)
    build_mod.main()

    # Publica no GitHub
    git_push()
