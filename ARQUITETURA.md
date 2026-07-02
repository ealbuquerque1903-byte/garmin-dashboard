# Garmin Dashboard — Arquitetura Completa, Mapa de Processos e Código

**Repositório:** github.com/ealbuquerque1903-byte/garmin-dashboard  
**Deploy:** github.io/ealbuquerque1903-byte/garmin-dashboard  
**Stack:** Python 3.11 · Jinja2 · Chart.js · GitHub Actions · GitHub Pages · PWA (iOS)

---

## 1. VISÃO GERAL — O QUE O SISTEMA FAZ

O usuário aperta **Atualizar** no app (PWA instalado no iPhone). Isso dispara um workflow no GitHub Actions que:
1. Autentica na Garmin Connect via OAuth tokens armazenados como Secrets
2. Baixa dados de bem-estar (sono, HRV, FC repouso, body battery, estresse, prontidão, passos) dos últimos 30 dias
3. Baixa atividades (corridas, treinos de força) com séries temporais, splits, clima e zonas de FC
4. Salva tudo em `garmin/history.json` no repositório (persistência entre syncs)
5. Gera um site estático com Jinja2 → pasta `dist/`
6. Publica a pasta `dist/` no branch `gh-pages` via peaceiris/actions-gh-pages
7. O app detecta a conclusão via polling na API do GitHub e recarrega a página automaticamente

---

## 2. MAPA DE PROCESSOS

```
[iPhone — PWA]
      │
      │  clica "Atualizar"
      ▼
[base.html — JavaScript]
  startSync()
      │  POST /repos/.../actions/workflows/sync.yml/dispatches
      ▼
[GitHub API]
      │  dispara workflow_dispatch
      ▼
[GitHub Actions — sync.yml]
  ┌─────────────────────────────────────────────────────────────┐
  │ 1. Checkout do branch main                                  │
  │ 2. pip install garminconnect==0.2.8 garth jinja2            │
  │ 3. Restaura tokens OAuth do Garmin (GARMIN_OAUTH1/2)        │
  │ 4. python3 sync.py 30                                       │
  │    ├── load_client() → carrega tokens + resolve display_name│
  │    ├── fetch_devices() → lista dispositivos pareados        │
  │    ├── fetch_hr_zone_limits() → zonas FC com pisos/tetos    │
  │    ├── fetch_wellness() × 30 dias (merge, não sobrescreve)  │
  │    └── fetch_activity() × N novas atividades                │
  │         ├── get_activity_details() → timeseries             │
  │         ├── get_activity_splits() → laps/splits             │
  │         ├── get_activity_hr_in_timezones() → zonas FC       │
  │         ├── get_activity_weather() → clima                  │
  │         └── connectapi(/activity-service) → summaryDTO      │
  │ 5. Verifica dados (acts > 0, wellness > 0)                  │
  │ 6. git commit garmin/history.json + git pull --rebase + push│
  │ 7. python3 build.py → gera dist/                            │
  │ 8. peaceiris/actions-gh-pages → publica dist/ no gh-pages   │
  └─────────────────────────────────────────────────────────────┘
      │  workflow status: completed/success
      ▼
[base.html — pollWorkflow()]
  aguarda 30s (propagação GitHub Pages)
      │
      ▼
[window.location.replace(url + ?t=timestamp)]
      │
      ▼
[Service Worker — sw.js]
  network-first para HTML → busca versão nova do servidor
  cache-first para assets estáticos (chart.min.js, icon)
      │
      ▼
[iPhone — página recarregada com dados novos]
```

---

## 3. ARQUIVOS E RESPONSABILIDADES

| Arquivo | Responsabilidade |
|---|---|
| `sync.py` | Coleta dados da Garmin Connect API, merge com histórico, salva history.json |
| `build.py` | Lê history.json, gera site estático em dist/ via Jinja2 |
| `templates/base.html` | Layout base, CSS, nav mobile/desktop, lógica de sync JS |
| `templates/index.html` | Dashboard: KPIs de hoje, gráficos 30 dias, lista de atividades |
| `templates/activity.html` | Página de detalhe de atividade: KPIs, gráficos, zonas FC, splits |
| `templates/wellness.html` | Página de detalhe de bem-estar por dia |
| `static/sw.js` | Service Worker: network-first HTML, cache-first assets, limpa caches antigos |
| `static/manifest.json` | PWA manifest para instalação no iOS/Android |
| `.github/workflows/sync.yml` | Pipeline CI/CD: sync → verify → commit → build → deploy |
| `garmin/history.json` | Banco de dados JSON persistido no repositório |

---

## 4. CÓDIGO COMPLETO

### 4.1 sync.py

```python
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
    """
    Busca lista de dispositivos pareados (Forerunner 970, HRM 600, etc.).
    Retorna [] se falhar — nunca quebra o sync.
    """
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
    """
    Busca pisos e tetos de cada zona de FC via /biometric-service/heartRateZones.
    Prefere sport=RUNNING, cai em DEFAULT se não encontrar.
    Retorna {} se falhar — history mantém valores anteriores (não sobrescreve).
    """
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
    """
    Carrega cliente Garmin a partir de tokens OAuth salvos.
    PROBLEMA CONHECIDO: ao carregar tokens sem login, client.display_name fica None.
    Os endpoints /userstats-service (RHR) e /wellness-service (steps) usam display_name
    na URL → retornam 403 com None.
    SOLUÇÃO: após load(), resolve display_name em dois passos:
      1. garth.profile (memória, pode ser None se garth não carregou perfil)
      2. connectapi(/userprofile-service/socialProfile) como fallback via rede
    """
    from garminconnect import Garmin
    token_path = Path(TOKEN_DIR)
    if not token_path.exists():
        print("Diretório .garmin_tokens não encontrado.")
        raise SystemExit(1)
    try:
        client = Garmin()
        client.garth.load(str(token_path))
        if not client.display_name:
            try:
                prof = client.garth.profile or {}
                client.display_name = prof.get("displayName", "")
            except Exception:
                pass
        if not client.display_name:
            try:
                prof = client.connectapi("/userprofile-service/socialProfile")
                client.display_name = (prof or {}).get("displayName", "")
            except Exception as e:
                print(f"  Aviso display_name: {e}")
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
    """
    Extrai séries temporais dos detalhes de atividade (get_activity_details).
    Mapeia métricas por índice para evitar dependência de ordem da API.
    """
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
    """
    Coleta dados de bem-estar para um dia específico.
    7 endpoints diferentes, cada um com try/except independente.
    Retorna dict com todos os campos — None se não disponível.
    
    MERGE STRATEGY (aplicada em sync(), não aqui):
    Nunca sobrescreve valor existente com None/"—"/[]/0.
    Isso evita que falhas parciais da API apaguem dados bons já salvos.
    """
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
    """
    Coleta dados completos de uma atividade.
    5 chamadas de API separadas com try/except individual:
    - get_activity_details: timeseries (FC, pace, cadência, altitude...)
    - get_activity_splits: splits por km (laps)
    - get_activity_hr_in_timezones: tempo em cada zona FC (fallback)
    - get_activity_weather: condições climáticas
    - connectapi(/activity-service/activity/{id}): summaryDTO (stamina, RPE, feel, device)
    
    Temperatura recebida em Fahrenheit → convertida para Celsius.
    device_id extraído do metadataDTO para identificar qual dispositivo registrou.
    hr_zone_times extraído do objeto da lista de atividades (mais confiável para Garmin).
    """
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

    stamina_start = summary_dto.get("beginPotentialStamina") or summary_dto.get("beginAvailableStamina")
    stamina_end   = summary_dto.get("endPotentialStamina")   or summary_dto.get("endAvailableStamina")
    stamina_min   = summary_dto.get("minAvailableStamina")

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
        if v >= 80:  return "Ótimo"
        if v >= 60:  return "Bom"
        if v >= 40:  return "Ok"
        if v >= 20:  return "Ruim"
        return "Péssimo"

    def f_to_c(f):
        if f is None: return None
        return round((float(f) - 32) * 5/9, 1)

    temp_c      = f_to_c(weather.get("temp"))
    apparent_c  = f_to_c(weather.get("apparentTemp"))
    dew_c       = f_to_c(weather.get("dewPoint"))

    hr_zone_times = [
        act.get("hrTimeInZone_1"), act.get("hrTimeInZone_2"),
        act.get("hrTimeInZone_3"), act.get("hrTimeInZone_4"),
        act.get("hrTimeInZone_5"),
    ]

    return {
        "id": str(act_id), "date": act_date,
        "name": act.get("activityName", "Atividade"),
        "type": act.get("activityType", {}).get("typeKey", "unknown"),
        "location": act.get("locationName"),
        "distance_km": round(float(dist_m) / 1000, 2) if dist_m else None,
        "pace": fmt_pace(speed), "avg_speed_mps": speed,
        "fastest_1k": fmt_duration(act.get("fastestSplit_1000")),
        "fastest_5k": fmt_duration(act.get("fastestSplit_5000")),
        "duration": fmt_duration(act.get("duration")),
        "duration_secs": act.get("duration"),
        "elapsed_duration": fmt_duration(act.get("elapsedDuration")),
        "moving_duration": fmt_duration(act.get("movingDuration")),
        "stopped_duration": fmt_duration((act.get("elapsedDuration") or 0) - (act.get("movingDuration") or 0)),
        "avg_hr": act.get("averageHR"), "max_hr": act.get("maxHR"),
        "avg_power": act.get("avgPower"), "normalized_power": act.get("normPower"),
        "max_power": act.get("maxPower"), "calories": act.get("calories"),
        "elevation_gain": act.get("elevationGain"), "elevation_loss": act.get("elevationLoss"),
        "cadence": act.get("averageRunningCadenceInStepsPerMinute") or act.get("averageBikingCadenceInRevPerMinute"),
        "max_cadence": act.get("maxRunningCadenceInStepsPerMinute"),
        "steps": act.get("steps"), "training_load": act.get("activityTrainingLoad"),
        "vo2max": act.get("vO2MaxValue"),
        "aerobic_te": act.get("aerobicTrainingEffect"),
        "anaerobic_te": act.get("anaerobicTrainingEffect"),
        "te_label": act.get("trainingEffectLabel"),
        "aerobic_te_msg": act.get("aerobicTrainingEffectMessage"),
        "anaerobic_te_msg": act.get("anaerobicTrainingEffectMessage"),
        "recovery_time": None,
        "stamina_start": stamina_start, "stamina_end": stamina_end, "stamina_min": stamina_min,
        "feel": feel_raw, "feel_label": feel_label(feel_raw),
        "rpe": rpe_raw, "rpe_label": rpe_label(rpe_raw),
        "recovery_hr": summary_dto.get("recoveryHeartRate"),
        "impact_load": summary_dto.get("impactLoad"),
        "body_battery_drain": summary_dto.get("differenceBodyBattery"),
        "avg_gct": act.get("avgGroundContactTime"),
        "avg_vertical_osc": act.get("avgVerticalOscillation"),
        "avg_vertical_ratio": act.get("avgVerticalRatio"),
        "avg_stride_length": act.get("avgStrideLength"),
        "temp_c": temp_c, "apparent_temp_c": apparent_c,
        "humidity": weather.get("relativeHumidity"), "dew_point_c": dew_c,
        "wind_speed": weather.get("windSpeed"), "wind_dir": weather.get("windDirectionCompassPoint"),
        "weather_desc": (weather.get("weatherTypeDTO") or {}).get("desc"),
        "min_temp_c": act.get("minTemperature"), "max_temp_c": act.get("maxTemperature"),
        "device_id": device_id,
        "hr_zone_times": hr_zone_times, "hr_zones": hr_zones,
        "timeseries": timeseries, "laps": laps,
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
    devices = fetch_devices(client)
    if devices:                        # só atualiza se retornou dados válidos
        history["devices"] = devices
    zones = fetch_hr_zone_limits(client)
    if zones:                          # só atualiza se retornou dados válidos
        history["hr_zone_limits"] = zones

    print(f"Bem-estar ({days} dias)...")
    for i in range(days):
        day = today - timedelta(days=i)
        ds  = day.isoformat()
        print(f"  {ds}", end=" ", flush=True)
        new_w    = fetch_wellness(client, day)
        existing = history["wellness"].get(ds, {})
        merged   = {**existing}
        for k, v in new_w.items():
            # Não sobrescreve com vazio/placeholder — protege dados bons já salvos
            if v is not None and v != [] and v != "" and v != "—":
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
```

---

### 4.2 build.py

```python
#!/usr/bin/env python3
import json, shutil, os
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

BASE = Path(__file__).parent
DIST = BASE / "dist"
DATA = BASE / "garmin" / "history.json"

def load():
    if not DATA.exists():
        raise SystemExit("ERRO: garmin/history.json não encontrado.")
    return json.loads(DATA.read_text(encoding="utf-8"))

def setup_dist():
    if DIST.exists(): shutil.rmtree(DIST)
    DIST.mkdir()
    static_src = BASE / "static"
    if static_src.exists():
        shutil.copytree(static_src, DIST / "static")

def env():
    return Environment(loader=FileSystemLoader(str(BASE / "templates")), autoescape=True)

_WELL_KEYS = ("sleep_score","hrv_avg","rhr","body_battery_charged","stress_avg","training_readiness_score","steps")

def sorted_wellness(history):
    """
    Ordena wellness por data desc e FILTRA dias completamente vazios.
    Razão: sync cria entrada para hoje mesmo sem dados ainda (relógio não sincronizou).
    Sem filtro, o dashboard "hoje" mostra tracinhos e o histórico tem linha vazia.
    """
    items = sorted(history.get("wellness", {}).items(), key=lambda x: x[0], reverse=True)
    return [(d, w) for d, w in items if any(w.get(k) for k in _WELL_KEYS)]

def sorted_activities(history):
    return sorted(history.get("activities", {}).values(),
                  key=lambda x: x.get("date", ""), reverse=True)

def clean_ts(lst):
    return json.dumps([v if v is not None else None for v in (lst or [])])

def build_index(jenv, history):
    wellness   = sorted_wellness(history)
    activities = sorted_activities(history)
    recent_w   = list(reversed(wellness[:30]))

    # "hoje" = primeiro dia com dados reais (wellness já filtrado)
    today_data = wellness[0][1] if wellness else {}

    chart_dates     = [w[0] for w in recent_w]
    chart_hrv       = [w[1].get("hrv_avg") for w in recent_w]
    chart_rhr       = [w[1].get("rhr") for w in recent_w]
    chart_sleep     = [w[1].get("sleep_score") for w in recent_w]
    chart_bb        = [w[1].get("body_battery_charged") for w in recent_w]
    chart_readiness = [w[1].get("training_readiness_score") for w in recent_w]

    act_sorted = sorted(activities, key=lambda x: x.get("date",""))[-30:]
    act_dates  = [a.get("date","") for a in act_sorted]
    act_loads  = [a.get("training_load") or 0 for a in act_sorted]
    act_dists  = [a.get("distance_km") or 0 for a in act_sorted]

    html = jenv.get_template("index.html").render(
        activities=activities, wellness=wellness[:14], today=today_data,
        chart_dates=json.dumps(chart_dates), chart_hrv=json.dumps(chart_hrv),
        chart_rhr=json.dumps(chart_rhr), chart_sleep=json.dumps(chart_sleep),
        chart_bb=json.dumps(chart_bb), chart_readiness=json.dumps(chart_readiness),
        act_dates=json.dumps(act_dates), act_loads=json.dumps(act_loads),
        act_dists=json.dumps(act_dists), static_prefix="static",
    )
    (DIST / "index.html").write_text(html, encoding="utf-8")

def build_activities(jenv, history):
    acts_dir       = DIST / "activity"
    acts_dir.mkdir()
    devices_map    = {d["id"]: d for d in history.get("devices", [])}
    hr_zone_limits = history.get("hr_zone_limits", {})
    for act_id, activity in history.get("activities", {}).items():
        wellness = history.get("wellness", {}).get(activity.get("date", ""), {})
        ts = activity.get("timeseries", {})
        html = jenv.get_template("activity.html").render(
            activity=activity, wellness=wellness,
            ts_time=clean_ts(ts.get("time")), ts_hr=clean_ts(ts.get("hr")),
            ts_pace=clean_ts(ts.get("pace")), ts_power=clean_ts(ts.get("power")),
            ts_cadence=clean_ts(ts.get("cadence")), ts_altitude=clean_ts(ts.get("altitude")),
            ts_distance=clean_ts(ts.get("distance")), ts_stamina=clean_ts(ts.get("stamina")),
            ts_temperature=clean_ts(ts.get("temperature")), ts_gct=clean_ts(ts.get("gct")),
            ts_vo=clean_ts(ts.get("vo")),
            has_power=json.dumps(any(v for v in (ts.get("power") or []) if v)),
            devices_map=devices_map, hr_zone_limits=hr_zone_limits,
            static_prefix="../static", index_href="../index.html",
        )
        (acts_dir / f"{act_id}.html").write_text(html, encoding="utf-8")

def build_wellness(jenv, history):
    well_dir = DIST / "wellness"
    well_dir.mkdir()
    for day, w in history.get("wellness", {}).items():
        html = jenv.get_template("wellness.html").render(
            wellness=w, day=day, static_prefix="../static", index_href="../index.html")
        (well_dir / f"{day}.html").write_text(html, encoding="utf-8")

def main():
    history = load()
    setup_dist()
    jenv = env()
    build_index(jenv, history)
    build_activities(jenv, history)
    build_wellness(jenv, history)

if __name__ == "__main__":
    main()
```

---

### 4.3 .github/workflows/sync.yml

```yaml
name: Garmin Sync

on:
  workflow_dispatch:   # ativado pelo botão Atualizar no app

jobs:
  sync:
    runs-on: ubuntu-latest
    permissions:
      contents: write   # necessário para fazer git push do history.json

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Python 3.11
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Instalar dependências
        run: pip install "garminconnect==0.2.8" garth jinja2
        # garminconnect==0.2.8 fixado para evitar quebras de API
        # garth é a lib OAuth subjacente (token storage/refresh)

      - name: Restaurar tokens do Garmin
        run: |
          mkdir -p .garmin_tokens
          echo '${{ secrets.GARMIN_OAUTH1 }}' > .garmin_tokens/oauth1_token.json
          echo '${{ secrets.GARMIN_OAUTH2 }}' > .garmin_tokens/oauth2_token.json
        # Secrets configurados em: repo → Settings → Secrets → Actions
        # GARMIN_OAUTH1: conteúdo de .garmin_tokens/oauth1_token.json
        # GARMIN_OAUTH2: conteúdo de .garmin_tokens/oauth2_token.json

      - name: Sincronizar dados
        run: python3 sync.py 30

      - name: Verificar dados sincronizados
        # Falha explicitamente se dados estiverem vazios — evita publicar site quebrado
        run: |
          python3 - <<'EOF'
          import json, sys
          h = json.load(open("garmin/history.json"))
          acts  = len(h.get("activities", {}))
          well  = len(h.get("wellness", {}))
          zones = bool(h.get("hr_zone_limits", {}).get("zones"))
          devs  = len(h.get("devices", []))
          today = sorted(h.get("wellness", {}).items(), reverse=True)
          today_data = next(((d,w) for d,w in today if w.get("sleep_score") or w.get("hrv_avg") or w.get("rhr")), None)
          print(f"✓ {acts} atividades | {well} dias bem-estar | zonas FC: {zones} | {devs} dispositivos")
          if today_data:
              d, w = today_data
              print(f"✓ Hoje ({d}): sono={w.get('sleep_score')} HRV={w.get('hrv_avg')} FC={w.get('rhr')} battery={w.get('body_battery_charged')}")
          if acts == 0:
              print("ERRO: nenhuma atividade sincronizada"); sys.exit(1)
          if well == 0:
              print("ERRO: nenhum dia de bem-estar"); sys.exit(1)
          EOF

      - name: Salvar history.json no repositório
        # git pull --rebase evita rejeição se outro commit chegou enquanto o sync rodava
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add garmin/history.json
          git diff --cached --quiet && echo "Sem alterações." || \
            (git commit -m "data: sync automático $(date -u '+%Y-%m-%d %H:%M UTC')" && \
             git pull --rebase origin main && \
             git push)

      - name: Gerar site estático
        run: python3 build.py

      - name: Publicar no GitHub Pages
        uses: peaceiris/actions-gh-pages@v4
        with:
          github_token: ${{ secrets.GITHUB_TOKEN }}
          publish_dir: ./dist
          force_orphan: true    # gh-pages sempre começa limpo (sem histórico acumulado)
          enable_jekyll: false  # evita processamento Jekyll que ignora arquivos com _
```

---

### 4.4 static/sw.js — Service Worker

```javascript
const CACHE = 'garmin-v3';
// v3 porque bumpar a versão força iOS a limpar caches antigos (v1, v2)
const STATIC = ['/garmin-dashboard/static/chart.min.js', '/garmin-dashboard/static/icon-180.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(STATIC)));
  self.skipWaiting(); // ativa imediatamente sem esperar páginas fecharem
});

self.addEventListener('activate', e => {
  // Deleta caches de versões antigas na ativação
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  
  // HTML (páginas): network-first
  // Razão: iOS PWA serve HTML do cache ao reabrir o app, mostrando versão antiga.
  // Network-first garante sempre a versão mais recente; cache só como fallback offline.
  if (e.request.destination === 'document' || url.pathname.endsWith('.html') || url.pathname.endsWith('/')) {
    e.respondWith(
      fetch(e.request, { cache: 'no-store' })
        .then(r => { const c = r.clone(); caches.open(CACHE).then(cache => cache.put(e.request, c)); return r; })
        .catch(() => caches.match(e.request))
    );
    return;
  }
  
  // Assets estáticos (JS, imagens): cache-first
  // Razão: chart.min.js não muda — servir do cache é mais rápido.
  e.respondWith(
    caches.match(e.request).then(cached => cached || fetch(e.request))
  );
});
```

---

### 4.5 templates/base.html — Lógica de Sync (JavaScript relevante)

```javascript
const REPO_OWNER = 'ealbuquerque1903-byte';
const REPO_NAME  = 'garmin-dashboard';
const WORKFLOW   = 'sync.yml';

async function startSync() {
  const token = getToken(); // lê do localStorage
  if (!token) { /* abre modal para inserir GitHub PAT */ return; }
  
  setSyncState('Iniciando...', '⏳', true);
  
  const triggerTime = Date.now();
  const res = await fetch(
    `https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/actions/workflows/${WORKFLOW}/dispatches`,
    { method: 'POST',
      headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ ref: 'main' }) }
  );
  
  if (res.status === 204) {
    pollWorkflow(token, triggerTime); // 204 = dispatch aceito
  }
}

async function pollWorkflow(token, triggerTime, attempt = 0) {
  const MAX_ATTEMPTS = 60; // 10 minutos máximo
  if (attempt >= MAX_ATTEMPTS) { setSyncState('Timeout', '↻', false); return; }
  
  // Aguarda antes de verificar (8s na 1ª vez, 10s nas seguintes)
  // Razão: GitHub Actions leva alguns segundos para registrar o run após o dispatch
  await new Promise(r => setTimeout(r, attempt === 0 ? 8000 : 10000));
  
  const data = await fetch(
    `https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/actions/runs?event=workflow_dispatch&per_page=5`,
    { headers: { 'Authorization': `Bearer ${token}` } }
  ).then(r => r.json());
  
  // Encontra o run disparado APÓS o clique (com 30s de tolerância para clock skew)
  const run = (data.workflow_runs || []).find(run =>
    new Date(run.created_at).getTime() >= triggerTime - 30000
  );
  
  if (run?.status === 'completed' && run?.conclusion === 'success') {
    // Aguarda 30s para GitHub Pages propagar o novo deploy antes de recarregar
    // Razão: workflow "completed" != páginas disponíveis no CDN do GitHub Pages
    let secs = 30;
    const tick = () => {
      setSyncState(`Publicando... (${secs}s)`, '✓', false);
      if (secs > 0) { secs--; setTimeout(tick, 1000); }
      else {
        const base = window.location.href.split('?')[0];
        window.location.replace(base + '?t=' + Date.now());
        // replace() ao invés de href para iOS não guardar a URL antiga no histórico
      }
    };
    tick();
  } else {
    pollWorkflow(token, triggerTime, attempt + 1);
  }
}
```

---

## 5. BUGS CORRIGIDOS E RAZÕES

| # | Bug | Causa Raiz | Solução |
|---|---|---|---|
| 1 | Dashboard vazio após deploy | `history.json` no repo estava com 0 atividades (foi esvaziado em sessão anterior e nunca commitado) | Adicionou step no workflow para commitar `history.json` após cada sync |
| 2 | KPIs "hoje" em branco | Sync roda em UTC, cria entrada para novo dia ainda sem dados; `wellness[0]` = dia vazio | `sorted_wellness()` filtra dias sem dados; "hoje" usa primeiro dia com dados reais |
| 3 | Wellness sobrescrito com vazio | `sync.py` sempre fazia `history["wellness"][ds] = fetch_wellness()` mesmo quando API retornou None | Estratégia de merge: só atualiza campo se novo valor é não-nulo/não-vazio/não-"—" |
| 4 | iOS não recarregava após sync | Timer fixo de 180s não coincidia com duração real do workflow; `location.href` cacheado pelo iOS | Polling real da API GitHub (10s interval); `location.replace()` com `?t=timestamp` |
| 5 | App exibia versão antiga ao reabrir | iOS PWA carrega `start_url` do cache em disco ao abrir do ícone | Service Worker com network-first para HTML; garante busca da versão mais nova |
| 6 | Zonas FC: ranges bpm sumiam | Havia dois blocos de template (um para `hr_zone_times`, outro para `hr_zones`) renderizando layouts diferentes; `hr_zone_limits` era sobrescrito com `{}` quando API falhava | Template unificado em um único bloco; devices/zones só sobrescritos se fetch retornar dados |
| 7 | RHR e steps não sincronizados (403) | `client.display_name` fica `None` ao carregar tokens sem login; endpoints `/userstats-service` e `/wellness-service` usam display_name na URL | Após `garth.load()`, resolve display_name via `garth.profile` ou `connectapi(/userprofile-service/socialProfile)` |
| 8 | Git push rejeitado no workflow | Sync demora ~3-4min; se outro commit chegou nesse período, push é rejeitado | `git pull --rebase origin main` antes do push |
| 9 | Countdown de 30s disparava em 31s | `if (secs-- > 0)` decrementa após checar → dispara quando secs=-1 | Corrigido para `if (secs > 0) { secs--; ... }` |
| 10 | Caches v1/v2 não limpos no iOS | Service Worker `activate` fazia só `clients.claim()`, sem limpar caches antigos | `activate` agora deleta todos os caches com nome diferente do atual (`garmin-v3`) |
| 11 | Merge bloqueava zeros válidos | Condição `v != 0` impedia salvar `stress_avg=0` ou `steps=0` no início do dia | Removido `v != 0` da condição de merge |

---

## 6. ESTRUTURA DE DADOS — history.json

```json
{
  "devices": [
    { "id": "123456", "name": "Forerunner 970", "firmware": "22.00", "primary": true },
    { "id": "789012", "name": "HRM-600", "firmware": "3.00", "primary": false }
  ],
  "hr_zone_limits": {
    "max_hr": 186,
    "zones": [
      { "floor": 92,  "ceil": 125 },
      { "floor": 126, "ceil": 137 },
      { "floor": 138, "ceil": 161 },
      { "floor": 162, "ceil": 171 },
      { "floor": 172, "ceil": 186 }
    ]
  },
  "wellness": {
    "2026-07-02": {
      "date": "2026-07-02",
      "sleep_score": 76,
      "sleep_duration": "5h 10min",
      "sleep_seconds": 18600,
      "deep_sleep": "1h 23min",
      "rem_sleep": "0h 57min",
      "hrv_avg": 47.0,
      "hrv_status": "BALANCED",
      "rhr": 50,
      "body_battery_charged": 58,
      "body_battery_drained": 3,
      "stress_avg": 11,
      "training_readiness_score": 61,
      "training_readiness_level": "MODERATE",
      "steps": 589
    }
  },
  "activities": {
    "23447682532": {
      "id": "23447682532",
      "date": "2026-07-01",
      "name": "Força",
      "type": "strength_training",
      "distance_km": null,
      "pace": "—",
      "duration": "35min 00s",
      "avg_hr": 112,
      "max_hr": 158,
      "calories": 220,
      "training_load": 45.2,
      "hr_zone_times": [120, 840, 600, 300, 240],
      "hr_zones": [...],
      "timeseries": { "time": [...], "hr": [...], "pace": [...] },
      "laps": [...]
    }
  }
}
```

---

## 7. APIS GARMIN CONNECT USADAS

| Endpoint / Método | Dado | Observação |
|---|---|---|
| `get_activities_by_date(start, end)` | Lista de atividades | Inclui `hrTimeInZone_1..5` |
| `get_activity_details(id, maxchart=2000)` | Timeseries (FC, pace, altitude...) | `metricDescriptors` + `activityDetailMetrics` |
| `get_activity_splits(id)` | Splits/laps | `lapDTOs` |
| `get_activity_hr_in_timezones(id)` | Tempo em zonas FC (alternativo) | Fallback se `hr_zone_times` vazio |
| `get_activity_weather(id)` | Clima | Temperatura em °F — convertida para °C |
| `connectapi(/activity-service/activity/{id})` | summaryDTO + metadataDTO | Stamina, RPE, feel, device_id |
| `get_sleep_data(date)` | Sono | `dailySleepDTO.sleepScores.overall.value` |
| `get_hrv_data(date)` | HRV | `hrvSummary.lastNightAvg` |
| `get_rhr_day(date)` | FC repouso | `allMetrics.metricsMap.WELLNESS_RESTING_HEART_RATE` — requer display_name na URL |
| `get_body_battery(date)` | Body battery | Lista com `charged` e `drained` |
| `get_stress_data(date)` | Estresse | `avgStressLevel` |
| `get_steps_data(date)` | Passos | Lista — requer display_name na URL |
| `get_training_readiness(date)` | Prontidão | Lista com `score` e `level` |
| `get_devices()` | Dispositivos pareados | `deviceId`, `productDisplayName`, firmware |
| `connectapi(/biometric-service/heartRateZones)` | Limites de zonas FC | Prefere sport=RUNNING |
| `connectapi(/userprofile-service/socialProfile)` | Perfil do usuário | Usado para resolver `display_name` |
