#!/usr/bin/env python3
"""
Garmin Sync — coleta treinos + bem-estar do Garmin Connect
e salva tudo em garmin/history.json
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

# ── client ──────────────────────────────────────────────────────────────────

def fetch_devices(client) -> list:
    try:
        devs = client.get_devices()
        result = []
        for d in (devs or []):
            result.append({
                "id":       str(d.get("deviceId", "")),
                "name":     d.get("productDisplayName") or d.get("deviceTypeSimpleName", ""),
                "firmware": d.get("currentFirmwareVersion", ""),
                "primary":  d.get("primaryActivityTrackerIndicator", False),
            })
        return result
    except Exception as e:
        print(f"  Aviso dispositivos: {e}")
        return []

def fetch_hr_zone_limits(client) -> dict:
    try:
        zones = client.connectapi('/biometric-service/heartRateZones')
        running = next((z for z in zones if z.get("sport") == "RUNNING"), None)
        default = next((z for z in zones if z.get("sport") == "DEFAULT"), None)
        z = running or default or {}
        f1, f2, f3, f4, f5, mx = (
            z.get("zone1Floor"), z.get("zone2Floor"), z.get("zone3Floor"),
            z.get("zone4Floor"), z.get("zone5Floor"), z.get("maxHeartRateUsed"),
        )
        return {
            "max_hr": mx,
            "zones": [
                {"floor": f1, "ceil": (f2 - 1) if f2 else None},
                {"floor": f2, "ceil": (f3 - 1) if f3 else None},
                {"floor": f3, "ceil": (f4 - 1) if f4 else None},
                {"floor": f4, "ceil": (f5 - 1) if f5 else None},
                {"floor": f5, "ceil": mx},
            ]
        }
    except Exception as e:
        print(f"  Aviso zonas FC: {e}")
        return {}

def load_client():
    from garminconnect import Garmin
    token_path = Path(TOKEN_DIR)
    if not token_path.exists():
        print("Diretório .garmin_tokens não encontrado.")
        raise SystemExit(1)
    try:
        client = Garmin()
        client.garth.load(str(token_path))
        print(f"Tokens carregados de {token_path}")
        return client
    except Exception as e:
        print(f"Erro ao carregar tokens: {e}")
        raise SystemExit(1)

# ── formatters ───────────────────────────────────────────────────────────────

def fmt_duration(seconds):
    if not seconds: return "—"
    s = int(seconds)
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}h {m:02d}min" if h else f"{m}min {sec:02d}s"

def fmt_pace(mps):
    if not mps: return "—"
    spk = 1000 / float(mps)
    return f"{int(spk)//60}:{int(spk)%60:02d} /km"

# ── timeseries ───────────────────────────────────────────────────────────────

def extract_timeseries(details: dict) -> dict:
    series = {"time": [], "hr": [], "pace": [], "power": [], "cadence": [],
              "altitude": [], "distance": [], "stamina": [], "temperature": [],
              "gct": [], "vo": [], "vr": [], "stride": [], "perf_cond": []}
    descriptors = details.get("metricDescriptors") or []
    metrics_pts  = details.get("activityDetailMetrics") or []
    if not descriptors or not metrics_pts:
        return series
    idx = {d["key"]: i for i, d in enumerate(descriptors)}

    def get_val(row, *keys):
        for k in keys:
            i = idx.get(k)
            if i is not None and i < len(row):
                v = row[i]
                if v is not None: return v
        return None

    for point in metrics_pts:
        row = point.get("metrics") or []
        elapsed = get_val(row, "sumElapsedDuration", "sumDuration")
        series["time"].append(round(elapsed, 1) if elapsed is not None else None)
        hr = get_val(row, "directHeartRate")
        series["hr"].append(round(hr) if hr is not None else None)
        speed = get_val(row, "directSpeed")
        series["pace"].append(round(1000 / float(speed), 1) if speed and float(speed) > 0.1 else None)
        power = get_val(row, "directPower", "sumAccumulatedPower")
        series["power"].append(round(power, 1) if power is not None else None)
        cadence = get_val(row, "directRunCadence", "directFractionalCadence", "directDoubleCadence")
        series["cadence"].append(round(cadence, 1) if cadence is not None else None)
        alt = get_val(row, "directElevation")
        series["altitude"].append(round(alt, 1) if alt is not None else None)
        dist = get_val(row, "sumDistance")
        series["distance"].append(round(dist, 1) if dist is not None else None)
        stam = get_val(row, "directAvailableStamina")
        series["stamina"].append(round(float(stam) * 100, 1) if stam is not None else None)
        temp = get_val(row, "directAirTemperature")
        series["temperature"].append(round(temp, 1) if temp is not None else None)
        gct = get_val(row, "directGroundContactTime")
        series["gct"].append(round(gct) if gct is not None else None)
        vo = get_val(row, "directVerticalOscillation")
        series["vo"].append(round(vo, 1) if vo is not None else None)
        vr = get_val(row, "directVerticalRatio")
        series["vr"].append(round(float(vr) * 100, 1) if vr is not None else None)
        sl = get_val(row, "directStrideLength")
        series["stride"].append(round(sl) if sl is not None else None)
        pc = get_val(row, "directPerformanceCondition")
        series["perf_cond"].append(round(pc) if pc is not None else None)
    return series

def extract_laps(lap_dtos: list) -> list:
    result = []
    for i, lap in enumerate(lap_dtos or []):
        dist = lap.get("distance", 0)
        result.append({
            "lap":              i + 1,
            "distance_km":      round(float(dist) / 1000, 2),
            "duration":         fmt_duration(lap.get("duration")),
            "moving_duration":  fmt_duration(lap.get("movingDuration")),
            "pace":             fmt_pace(lap.get("averageSpeed")),
            "avg_hr":           lap.get("averageHR"),
            "max_hr":           lap.get("maxHR"),
            "calories":         lap.get("calories"),
            "elevation_gain":   lap.get("elevationGain"),
            "elevation_loss":   lap.get("elevationLoss"),
            "avg_power":        lap.get("averagePower"),
            "normalized_power": lap.get("normalizedPower"),
            "avg_cadence":      lap.get("averageRunCadence") or lap.get("averageBikingCadenceInRevPerMinute"),
            "avg_temp":         lap.get("averageTemperature"),
            "gct":              lap.get("groundContactTime"),
            "vertical_osc":     lap.get("verticalOscillation"),
            "vertical_ratio":   lap.get("verticalRatio"),
            "stride_length":    lap.get("strideLength"),
        })
    return result

# ── wellness ─────────────────────────────────────────────────────────────────

def fetch_wellness(client, day: date) -> dict:
    ds = day.isoformat()
    raw = {}
    for key, fn in [
        ("sleep",              lambda: client.get_sleep_data(ds)),
        ("hrv",                lambda: client.get_hrv_data(ds)),
        ("heart_rate",         lambda: client.get_rhr_day(ds)),
        ("body_battery",       lambda: client.get_body_battery(ds)),
        ("stress",             lambda: client.get_stress_data(ds)),
        ("steps",              lambda: client.get_steps_data(ds)),
        ("training_readiness", lambda: client.get_training_readiness(ds)),
    ]:
        try:   raw[key] = fn()
        except Exception: raw[key] = {} if key in ("sleep","hrv","heart_rate","stress") else []

    sleep_dto  = (raw["sleep"].get("dailySleepDTO") or {}) if isinstance(raw["sleep"], dict) else {}
    hrv_sum    = (raw["hrv"].get("hrvSummary") or {})       if isinstance(raw["hrv"], dict)   else {}
    body_list  = raw["body_battery"] if isinstance(raw["body_battery"], list) else []
    tr_list    = raw["training_readiness"] if isinstance(raw["training_readiness"], list) else []
    steps_list = raw["steps"] if isinstance(raw["steps"], list) else []

    def secs_to_str(s):
        if not s: return None
        return f"{int(s)//3600}h {(int(s)%3600)//60:02d}min"

    rhr = None
    try:
        m = raw["heart_rate"].get("allMetrics", {}).get("metricsMap", {})
        rhr_list = m.get("WELLNESS_RESTING_HEART_RATE", [])
        if rhr_list: rhr = int(rhr_list[0]["value"])
    except Exception: pass

    sleep_secs = sleep_dto.get("sleepTimeSeconds")
    deep_secs  = sleep_dto.get("deepSleepSeconds")
    rem_secs   = sleep_dto.get("remSleepSeconds")

    return {
        "date":                     ds,
        "sleep_score":              (sleep_dto.get("sleepScores") or {}).get("overall", {}).get("value"),
        "sleep_duration":           secs_to_str(sleep_secs),
        "sleep_seconds":            sleep_secs,
        "deep_sleep":               secs_to_str(deep_secs),
        "rem_sleep":                secs_to_str(rem_secs),
        "hrv_avg":                  raw["sleep"].get("avgOvernightHrv") or hrv_sum.get("lastNightAvg") if isinstance(raw["sleep"], dict) else hrv_sum.get("lastNightAvg"),
        "hrv_status":               hrv_sum.get("status"),
        "rhr":                      rhr,
        "body_battery_charged":     body_list[0].get("charged") if body_list else None,
        "body_battery_drained":     body_list[0].get("drained") if body_list else None,
        "stress_avg":               raw["stress"].get("avgStressLevel") if isinstance(raw["stress"], dict) else None,
        "training_readiness_score": tr_list[0].get("score") if tr_list else None,
        "training_readiness_level": tr_list[0].get("level") if tr_list else None,
        "steps":                    sum(s.get("steps", 0) for s in steps_list),
    }

# ── activity ─────────────────────────────────────────────────────────────────

def fetch_activity(client, act: dict) -> dict:
    act_id   = act.get("activityId")
    act_date = (act.get("startTimeLocal") or "")[:10]
    print(f"    → timeseries, splits, weather...")

    details = {}; lap_dtos = []; hr_zones = {}; weather = {}; summary_dto = {}; device_id = ""
    try:    details  = client.get_activity_details(act_id, maxchart=2000)
    except Exception as e: print(f"      detalhe: {e}")
    try:
        splits = client.get_activity_splits(act_id)
        lap_dtos = splits.get("lapDTOs") or []
    except Exception: pass
    try:    hr_zones = client.get_activity_hr_in_timezones(act_id)
    except Exception: pass
    try:    weather  = client.get_activity_weather(act_id) or {}
    except Exception: pass
    try:
        act_full = client.connectapi(f"/activity-service/activity/{act_id}")
        summary_dto = act_full.get("summaryDTO") or {}
        metadata_dto = act_full.get("metadataDTO") or {}
        device_id = str((metadata_dto.get("deviceMetaDataDTO") or {}).get("deviceId", ""))
    except Exception: pass

    timeseries = extract_timeseries(details)
    laps       = extract_laps(lap_dtos)
    dist_m     = act.get("distance")
    speed      = act.get("averageSpeed")

    # Stamina do summaryDTO (mais confiável que timeseries)
    stamina_start = summary_dto.get("beginPotentialStamina") or summary_dto.get("beginAvailableStamina")
    stamina_end   = summary_dto.get("endPotentialStamina")   or summary_dto.get("endAvailableStamina")
    stamina_min   = summary_dto.get("minAvailableStamina")

    # Autoavaliação: feel 0-100 → converte para 1-10, RPE 0-100 → 1-10
    feel_raw = summary_dto.get("directWorkoutFeel")
    rpe_raw  = summary_dto.get("directWorkoutRpe")

    def rpe_label(v):
        if v is None: return None
        if v <= 20:  return "Muito fácil"
        if v <= 35:  return "Fácil"
        if v <= 50:  return "Moderado"
        if v <= 65:  return "Difícil"
        if v <= 80:  return "Muito difícil"
        return "Máximo"

    def feel_label(v):
        if v is None: return None
        if v >= 80:  return "Ótimo 😄"
        if v >= 60:  return "Bom 🙂"
        if v >= 40:  return "Ok 😐"
        if v >= 20:  return "Ruim 😕"
        return "Péssimo 😣"

    # Temperatura em Fahrenheit → Celsius
    def f_to_c(f):
        if f is None: return None
        return round((float(f) - 32) * 5/9, 1)

    temp_c      = f_to_c(weather.get("temp"))
    apparent_c  = f_to_c(weather.get("apparentTemp"))
    dew_c       = f_to_c(weather.get("dewPoint"))

    # Zonas FC em tempo (segundos) — do get_activities
    hr_zone_times = [
        act.get("hrTimeInZone_1"), act.get("hrTimeInZone_2"),
        act.get("hrTimeInZone_3"), act.get("hrTimeInZone_4"),
        act.get("hrTimeInZone_5"),
    ]

    return {
        "id":               str(act_id),
        "date":             act_date,
        "name":             act.get("activityName", "Atividade"),
        "type":             act.get("activityType", {}).get("typeKey", "unknown"),
        "location":         act.get("locationName"),
        # Distância e ritmo
        "distance_km":      round(float(dist_m) / 1000, 2) if dist_m else None,
        "pace":             fmt_pace(speed),
        "avg_speed_mps":    speed,
        "fastest_1k":       fmt_duration(act.get("fastestSplit_1000")),
        "fastest_5k":       fmt_duration(act.get("fastestSplit_5000")),
        # Tempo
        "duration":         fmt_duration(act.get("duration")),
        "duration_secs":    act.get("duration"),
        "elapsed_duration": fmt_duration(act.get("elapsedDuration")),
        "moving_duration":  fmt_duration(act.get("movingDuration")),
        "stopped_duration": fmt_duration((act.get("elapsedDuration") or 0) - (act.get("movingDuration") or 0)),
        # FC
        "avg_hr":           act.get("averageHR"),
        "max_hr":           act.get("maxHR"),
        # Potência
        "avg_power":        act.get("avgPower"),
        "normalized_power": act.get("normPower"),
        "max_power":        act.get("maxPower"),
        # Outros KPIs
        "calories":         act.get("calories"),
        "elevation_gain":   act.get("elevationGain"),
        "elevation_loss":   act.get("elevationLoss"),
        "cadence":          act.get("averageRunningCadenceInStepsPerMinute") or act.get("averageBikingCadenceInRevPerMinute"),
        "max_cadence":      act.get("maxRunningCadenceInStepsPerMinute"),
        "steps":            act.get("steps"),
        "training_load":    act.get("activityTrainingLoad"),
        "vo2max":           act.get("vO2MaxValue"),
        # Efeitos de treino
        "aerobic_te":       act.get("aerobicTrainingEffect"),
        "anaerobic_te":     act.get("anaerobicTrainingEffect"),
        "te_label":         act.get("trainingEffectLabel"),
        "aerobic_te_msg":   act.get("aerobicTrainingEffectMessage"),
        "anaerobic_te_msg": act.get("anaerobicTrainingEffectMessage"),
        # Tempo de recuperação (em horas — busca via wellness de recuperação)
        "recovery_time":    None,  # preenchido pelo wellness se disponível
        # Stamina
        "stamina_start":    stamina_start,
        "stamina_end":      stamina_end,
        "stamina_min":      stamina_min,
        # Autoavaliação
        "feel":             feel_raw,
        "feel_label":       feel_label(feel_raw),
        "rpe":              rpe_raw,
        "rpe_label":        rpe_label(rpe_raw),
        "recovery_hr":      summary_dto.get("recoveryHeartRate"),
        # Impacto
        "impact_load":      summary_dto.get("impactLoad"),
        "body_battery_drain": summary_dto.get("differenceBodyBattery"),
        # Dinâmica de corrida
        "avg_gct":          act.get("avgGroundContactTime"),
        "avg_vertical_osc": act.get("avgVerticalOscillation"),
        "avg_vertical_ratio": act.get("avgVerticalRatio"),
        "avg_stride_length":  act.get("avgStrideLength"),
        # Temperatura
        "temp_c":           temp_c,
        "apparent_temp_c":  apparent_c,
        "humidity":         weather.get("relativeHumidity"),
        "dew_point_c":      dew_c,
        "wind_speed":       weather.get("windSpeed"),
        "wind_dir":         weather.get("windDirectionCompassPoint"),
        "weather_desc":     (weather.get("weatherTypeDTO") or {}).get("desc"),
        "min_temp_c":       act.get("minTemperature"),
        "max_temp_c":       act.get("maxTemperature"),
        # Dispositivo
        "device_id":        device_id,
        # Zonas FC (tempo em segundos)
        "hr_zone_times":    hr_zone_times,
        "hr_zones":         hr_zones,
        "timeseries":       timeseries,
        "laps":             laps,
    }

# ── history ───────────────────────────────────────────────────────────────────

def load_history() -> dict:
    if DB_FILE.exists():
        try: return json.loads(DB_FILE.read_text(encoding="utf-8"))
        except Exception: pass
    return {"wellness": {}, "activities": {}}

def save_history(history: dict):
    DB_FILE.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")

# ── main sync ─────────────────────────────────────────────────────────────────

def sync(days: int = 30):
    client = load_client()
    print(f"Conectado como: {client.get_full_name()}\n")

    GARMIN_DIR.mkdir(exist_ok=True)
    history = load_history()
    today   = date.today()

    print("Dispositivos e zonas FC...")
    history["devices"]        = fetch_devices(client)
    history["hr_zone_limits"] = fetch_hr_zone_limits(client)

    print(f"Bem-estar ({days} dias)...")
    for i in range(days):
        day = today - timedelta(days=i)
        ds  = day.isoformat()
        print(f"  {ds}", end=" ", flush=True)
        new_w    = fetch_wellness(client, day)
        existing = history["wellness"].get(ds, {})
        merged   = {**existing}
        for k, v in new_w.items():
            if v is not None and v != 0 and v != [] and v != "":
                merged[k] = v
        history["wellness"][ds] = merged
        print("✓")

    start_str = (today - timedelta(days=days)).isoformat()
    print(f"\nAtividades ({days} dias)...")
    try:    activities = client.get_activities_by_date(start_str, today.isoformat())
    except Exception as e: print(f"  Aviso: {e}"); activities = []

    for act in activities:
        act_id   = str(act.get("activityId", ""))
        act_name = act.get("activityName", "?")
        act_date = (act.get("startTimeLocal") or "")[:10]
        if act_id in history["activities"]:
            print(f"  {act_date} — {act_name} [já sincronizado]")
            continue
        print(f"  {act_date} — {act_name}")
        history["activities"][act_id] = fetch_activity(client, act)
        print(f"    ✓")

    save_history(history)
    print(f"\nConcluído! {days} dias, {len(activities)} atividade(s).")

if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    sync(days)
