#!/usr/bin/env python3
"""
run_calibrated.py
=================
Orquestador del Benchmark Calibrado en Modo Automatizado.
IT2iD - Bases de Datos en la Nube 2026

Ejecuta la matriz de mediciones a través de los 3 perfiles de Docker:
  1. PROFILE_A_BASELINE  (4.0 CPUs, 4096M RAM)
  2. PROFILE_B_MEDIUM    (2.0 CPUs, 2048M RAM)
  3. PROFILE_C_SEVERE    (1.0 CPU,  1024M RAM)

Ajustes calibrados:
  - Warmup: 3 s
  - Medición: 15 s por celda
  - Cooldown: 2 s
  - Concurrencias: [1, 10, 50]
  - Repeticiones: 1

Al finalizar genera automáticamente el reporte consolidado y todos los gráficos.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

def run_cmd(cmd_list, timeout=120):
    print(f"[CMD] {' '.join(cmd_list)}")
    res = subprocess.run(cmd_list, capture_output=True, text=True, timeout=timeout, cwd=str(BASE_DIR))
    if res.returncode != 0:
        print(f"[WARN] Command exited with code {res.returncode}: {res.stderr.strip()[:200]}")
    return res

def wait_for_services():
    import psycopg, pymongo
    print("[WAIT] Esperando que MongoDB y PostgreSQL respondan...")
    # MongoDB
    for _ in range(25):
        try:
            client = pymongo.MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=2000)
            client.admin.command('ping')
            client.close()
            break
        except Exception:
            time.sleep(1.5)
    else:
        print("[ERROR] MongoDB no respondió.")
        return False

    # PostgreSQL
    for _ in range(30):
        try:
            with psycopg.connect("host=localhost port=5433 dbname=benchmark user=bench_user password=bench_pass connect_timeout=4") as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1;")
            break
        except Exception as e:
            time.sleep(1.5)
    else:
        print("[ERROR] PostgreSQL no respondió.")
        return False

    print("[OK] Ambos motores responden correctamente.")
    return True

def switch_profile(target_profile):
    print("\n" + "=" * 70)
    print(f"  CAMBIANDO A PERFIL: {target_profile.upper()}")
    print("=" * 70)

    # Remover contenedores de bases de datos para liberar puertos sin tocar los volúmenes
    run_cmd(["docker", "rm", "-f", "mongo_baseline", "postgres_baseline", "mongo_medium", "postgres_medium", "mongo_severe", "postgres_severe"])

    # Levantar el perfil objetivo
    run_cmd(["docker-compose", "--profile", target_profile, "up", "-d"])

    # Esperar servicios
    if not wait_for_services():
        sys.exit(1)

def main():
    csv_path = BASE_DIR / "results" / "benchmark_results.csv"
    if csv_path.exists():
        csv_path.unlink() # Limpiar CSV para inicio fresco

    profiles = [
        ("baseline", "PROFILE_A_BASELINE"),
        ("medium",   "PROFILE_B_MEDIUM"),
        ("severe",   "PROFILE_C_SEVERE")
    ]

    total_start = time.time()

    for p_flag, p_name in profiles:
        switch_profile(p_flag)

        # Ejecutar runner para este perfil
        print(f"\n[BENCH] Iniciando mediciones para {p_name}...")
        cmd = [
            sys.executable,
            str(BASE_DIR / "benchmark_runner.py"),
            "--profile", p_flag,
            "--warmup", "3",
            "--measure", "15",
            "--cooldown", "2",
            "--concurrencies", "1", "10", "50",
            "--repetitions", "1"
        ]
        res = subprocess.run(cmd, cwd=str(BASE_DIR))
        if res.returncode != 0:
            print(f"[ERROR] Error al ejecutar benchmark para perfil {p_name}")

    total_elapsed = time.time() - total_start
    print("\n" + "=" * 70)
    print(f"  TODAS LAS MEDICIONES COMPLETADAS en {total_elapsed/60:.1f} minutos!")
    print("=" * 70)

    # Generar reporte final y gráficos
    print("\n[REPORT] Generando reporte de métricas y gráficos...")
    rep_cmd = [
        sys.executable,
        str(BASE_DIR / "scripts" / "analyze_results.py"),
        "--csv", str(csv_path),
        "--out-dir", str(BASE_DIR / "reports")
    ]
    subprocess.run(rep_cmd, cwd=str(BASE_DIR))
    print("[OK] Reporte y gráficos actualizados exitosamente en reports/")

if __name__ == "__main__":
    main()

