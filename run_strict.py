#!/usr/bin/env python3
"""
run_strict.py
=============
Orquestador del Benchmark Estricto del PDF (Protocolo Completo de ~18-20 Horas).
IT2iD - Bases de Datos en la Nube 2026

Protocolo Estricto (PDF §6):
  - Warmup: 60 s
  - Medición: 300 s (5 minutos por corrida)
  - Cooldown: 60 s
  - Concurrencias: [1, 10, 50] clientes
  - Repeticiones: 3 por cada celda

Matriz por perfil:
  2 motores × 3 queries × 3 concurrencias × 3 repeticiones = 54 ejecuciones por perfil
  3 perfiles × 54 = 162 ejecuciones totales
"""

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

def log(msg: str):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}", flush=True)

def run_cmd(cmd_list, timeout=120):
    log(f"[CMD] {' '.join(cmd_list)}")
    res = subprocess.run(cmd_list, capture_output=True, text=True, timeout=timeout, cwd=str(BASE_DIR))
    if res.returncode != 0:
        log(f"[WARN] Command exited with code {res.returncode}: {res.stderr.strip()[:200]}")
    return res

def wait_for_services():
    import psycopg, pymongo
    log("[WAIT] Esperando que MongoDB y PostgreSQL respondan...")
    # MongoDB
    for _ in range(30):
        try:
            client = pymongo.MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=2000)
            client.admin.command('ping')
            client.close()
            break
        except Exception:
            time.sleep(2)
    else:
        log("[ERROR] MongoDB no respondió.")
        return False

    # PostgreSQL
    for _ in range(35):
        try:
            with psycopg.connect("host=localhost port=5433 dbname=benchmark user=bench_user password=bench_pass connect_timeout=4") as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1;")
            break
        except Exception:
            time.sleep(2)
    else:
        log("[ERROR] PostgreSQL no respondió.")
        return False

    log("[OK] Ambos motores responden correctamente.")
    return True

def switch_profile(target_profile):
    log("\n" + "=" * 70)
    log(f"  CAMBIANDO A PERFIL: {target_profile.upper()}")
    log("=" * 70)

    # Remover contenedores de bases de datos para liberar puertos sin tocar los volúmenes
    run_cmd(["docker", "rm", "-f", "mongo_baseline", "postgres_baseline", "mongo_medium", "postgres_medium", "mongo_severe", "postgres_severe"])

    # Levantar el perfil objetivo
    run_cmd(["docker-compose", "--profile", target_profile, "up", "-d"])

    # Esperar servicios
    if not wait_for_services():
        log("[FATAL] Los servicios no estuvieron listos.")
        sys.exit(1)

def get_completed_counts(csv_path):
    import csv
    counts = {}
    if csv_path.exists():
        try:
            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    p = r.get("profile")
                    counts[p] = counts.get(p, 0) + 1
        except Exception:
            pass
    return counts

def main():
    csv_path = BASE_DIR / "results" / "benchmark_results.csv"

    profiles = [
        ("baseline", "PROFILE_A_BASELINE"),
        ("medium",   "PROFILE_B_MEDIUM"),
        ("severe",   "PROFILE_C_SEVERE")
    ]

    total_start = time.time()
    log("======================================================================")
    log("  INICIANDO PROTOCOLO ESTRICTO DE BENCHMARK (PDF §6)")
    log("  Dataset      : 500,000 registros")
    log("  Warmup       : 60 s")
    log("  Medición     : 300 s (5 min)")
    log("  Cooldown     : 60 s")
    log("  Repeticiones : 3 por celda")
    log("  Concurrencias: 1, 10, 50 clientes")
    log("======================================================================")

    for idx, (p_flag, p_name) in enumerate(profiles, start=1):
        completed = get_completed_counts(csv_path).get(p_name, 0)
        if completed >= 54:
            log(f"\n[SKIP] Perfil {idx}/3 ({p_name}) ya tiene {completed}/54 celdas completadas. Saltando al siguiente...")
            continue

        p_start = time.time()
        log(f"\n>>> INICIANDO ESCENARIOS DEL PERFIL {idx}/3: {p_name} (Progreso actual: {completed}/54) <<<")
        switch_profile(p_flag)

        cmd = [
            sys.executable,
            "-u",
            str(BASE_DIR / "benchmark_runner.py"),
            "--profile", p_flag,
            "--warmup", "60",
            "--measure", "300",
            "--cooldown", "60",
            "--concurrencies", "1", "10", "50",
            "--repetitions", "3"
        ]
        res = subprocess.run(cmd, cwd=str(BASE_DIR))
        if res.returncode != 0:
            log(f"[ERROR] Error al ejecutar mediciones para perfil {p_name}")

        p_elapsed = (time.time() - p_start) / 3600
        log(f"[OK] Perfil {p_name} completado en {p_elapsed:.2f} horas.")

        # Generar reporte intermedio
        log("[REPORT] Actualizando reporte intermedio y gráficos...")
        rep_cmd = [
            sys.executable,
            str(BASE_DIR / "scripts" / "analyze_results.py"),
            "--csv", str(csv_path),
            "--out-dir", str(BASE_DIR / "reports")
        ]
        subprocess.run(rep_cmd, cwd=str(BASE_DIR))

    total_elapsed = (time.time() - total_start) / 3600
    log("\n" + "=" * 70)
    log(f"  PROTOCOLO ESTRICTO FINALIZADO EXITOSAMENTE en {total_elapsed:.2f} horas!")
    log("======================================================================")

if __name__ == "__main__":
    main()

