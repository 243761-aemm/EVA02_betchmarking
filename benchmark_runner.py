#!/usr/bin/env python3
"""
benchmark_runner.py
====================
Runner de benchmark multiproceso para la matriz de mediciones.
IT2iD - Bases de Datos en la Nube 2026

Implementación con drivers nativos de alto rendimiento (pymongo & psycopg)
para medición precisa de latencia a nivel de milisegundos sin sobrecosto de CLI.

Matriz completa (PDF §6):
  2 motores × 3 perfiles × 2 queries × 3 concurrencias = 36 celdas
  Cada celda × 3 repeticiones = 108 ejecuciones totales

Protocolo por celda:
  1. Flush de caché
  2. Warmup: 60 s
  3. Medición: 300 s
  4. Cooldown: 60 s

KPIs recolectados:
  throughput_qps, latency_p50/p95/p99_ms,
  cpu_throttled_seconds, max_rss_mb, disk_spill, status

Salida: results/benchmark_results.csv (esquema del PDF §8.2)
"""

import argparse
import csv
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import urllib.request
import urllib.parse

import pymongo
import psycopg

# ---------------------------------------------------------------------------
# Conexión a bases de datos y Prometheus
# ---------------------------------------------------------------------------
MONGO_HOST = os.getenv("MONGO_HOST", "localhost")
MONGO_PORT = int(os.getenv("MONGO_PORT", "27017"))
MONGO_DB   = os.getenv("MONGO_DB",   "benchmark")

PG_HOST = os.getenv("PG_HOST",   "localhost")
PG_PORT = int(os.getenv("PG_PORT", "5433"))
PG_DB   = os.getenv("PG_DB",     "benchmark")
PG_USER = os.getenv("PG_USER",   "bench_user")
PG_PASS = os.getenv("PG_PASS",   "bench_pass")

PROM_URL = os.getenv("PROM_URL", "http://localhost:9090")

# ---------------------------------------------------------------------------
# Definición de Queries (PDF §5)
# ---------------------------------------------------------------------------
Q1_MONGO_PIPELINE = [
    { "$unwind": "$items" },
    { "$match": { "items.price": { "$gt": 50 } } },
    {
      "$group": {
        "_id": {
          "country":  "$customer.geo.country",
          "category": "$items.category"
        },
        "total_revenue": { "$sum": { "$multiply": ["$items.qty", "$items.price"] } },
        "avg_revenue":   { "$avg": { "$multiply": ["$items.qty", "$items.price"] } },
        "count":         { "$sum": 1 }
      }
    }
]

Q2_MONGO_PIPELINE = [
    {
      "$match": {
        "payment.status": "completed",
        "timestamp": { "$gte": "2026-01-01T00:00:00Z" }
      }
    },
    { "$sort": { "payment.final_total": -1 } },
    { "$limit": 1000 },
    {
      "$project": {
        "order_id":            1,
        "customer.id":         1,
        "payment.final_total": 1
      }
    }
]

Q1_PG_SQL = """
SELECT
    data->'customer'->'geo'->>'country' AS country,
    item->>'category' AS category,
    SUM((item->>'qty')::numeric * (item->>'price')::numeric) AS total_revenue,
    AVG((item->>'qty')::numeric * (item->>'price')::numeric) AS avg_revenue,
    COUNT(*) AS count
FROM orders, jsonb_array_elements(data->'items') AS item
WHERE (item->>'price')::numeric > 50
GROUP BY 1, 2;
"""

Q2_PG_SQL = """
SELECT
    data->>'order_id' AS order_id,
    data->'customer'->>'id' AS customer_id,
    (data->'payment'->>'final_total')::numeric AS final_total
FROM orders
WHERE data->'payment'->>'status' = 'completed'
  AND (data->>'timestamp')::timestamptz >= '2026-01-01 00:00:00Z'
ORDER BY (data->'payment'->>'final_total')::numeric DESC
LIMIT 1000;
"""

# ---------------------------------------------------------------------------
# Perfiles y configuración de la matriz
# ---------------------------------------------------------------------------
ENGINES = ["mongodb", "postgresql"]

PROFILES = {
    "PROFILE_A_BASELINE": {
        "container_mongo": "mongo_baseline",
        "container_pg":    "postgres_baseline",
        "cpus": 4.0,
        "mem_mb": 4096,
    },
    "PROFILE_B_MEDIUM": {
        "container_mongo": "mongo_medium",
        "container_pg":    "postgres_medium",
        "cpus": 2.0,
        "mem_mb": 2048,
    },
    "PROFILE_C_SEVERE": {
        "container_mongo": "mongo_severe",
        "container_pg":    "postgres_severe",
        "cpus": 1.0,
        "mem_mb": 1024,
    },
}

QUERIES = {
    "Q1": {
        "type": "Q1",
        "allow_disk_use": True,
        "work_mem": "64MB",
        "description": "Unwind, Filter & Grouping (saturación de CPU)",
    },
    "Q2_disk_false": {
        "type": "Q2",
        "allow_disk_use": False,
        "work_mem": "4MB",
        "description": "Massive Sort & Top-K (estrés de buffer / spill)",
    },
    "Q2_disk_true": {
        "type": "Q2",
        "allow_disk_use": True,
        "work_mem": "64MB",
        "description": "Massive Sort & Top-K (con buffer holgado)",
    },
}

CONCURRENCIES = [1, 10, 50]          # PDF §6
REPETITIONS   = 3                     # PDF §6
WARMUP_S      = 60                    # PDF §6
MEASURE_S     = 300                   # PDF §6
COOLDOWN_S    = 60                    # PDF §6

CSV_HEADER = [
    "timestamp_epoch",
    "engine",
    "profile",
    "query_id",
    "concurrency",
    "run_number",
    "throughput_qps",
    "latency_p50_ms",
    "latency_p95_ms",
    "latency_p99_ms",
    "cpu_throttled_seconds",
    "max_rss_mb",
    "disk_spill",
    "status",
]

# ---------------------------------------------------------------------------
# Helpers Prometheus
# ---------------------------------------------------------------------------
def query_prometheus(promql: str) -> float:
    try:
        url = f"{PROM_URL}/api/v1/query?query={urllib.parse.quote(promql)}"
        with urllib.request.urlopen(url, timeout=4) as resp:
            data = json.loads(resp.read())
            results = data.get("data", {}).get("result", [])
            if results:
                return float(results[0]["value"][1])
    except Exception:
        pass
    return 0.0

def get_container_metrics(container_name: str) -> dict:
    cpu_throttled = query_prometheus(
        f'container_cpu_cfs_throttled_seconds_total{{name="{container_name}"}}'
    )
    rss_bytes = query_prometheus(
        f'container_memory_rss{{name="{container_name}"}}'
    )
    if rss_bytes == 0.0:
        rss_bytes = query_prometheus(
            f'container_memory_usage_bytes{{name="{container_name}"}}'
        )
    return {
        "cpu_throttled_seconds": round(cpu_throttled, 4),
        "max_rss_mb": round(rss_bytes / (1024 * 1024), 2),
    }

def flush_cache(container_name: str) -> None:
    try:
        subprocess.run(
            ["docker", "exec", container_name, "sh", "-c", "sync; echo 3 > /proc/sys/vm/drop_caches"],
            capture_output=True, timeout=10
        )
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Ejecución directa de queries con drivers nativos
# ---------------------------------------------------------------------------
def exec_mongo_query(client: pymongo.MongoClient, q_conf: dict) -> tuple[float, bool]:
    q_type = q_conf["type"]
    allow_disk = q_conf["allow_disk_use"]
    db = client[MONGO_DB]
    t0 = time.perf_counter()
    try:
        if q_type == "Q1":
            cursor = db.orders.aggregate(Q1_MONGO_PIPELINE, allowDiskUse=True)
        else:
            cursor = db.orders.aggregate(Q2_MONGO_PIPELINE, allowDiskUse=allow_disk)
        _ = list(cursor)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return elapsed_ms, False
    except Exception as e:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return elapsed_ms, True

def exec_pg_query(conn_str: str, q_conf: dict) -> tuple[float, bool, bool]:
    q_type = q_conf["type"]
    work_mem = q_conf["work_mem"]
    t0 = time.perf_counter()
    disk_spill = (q_type == "Q2" and work_mem == "4MB")
    try:
        with psycopg.connect(conn_str) as conn:
            with conn.cursor() as cur:
                if work_mem:
                    cur.execute(f"SET work_mem = '{work_mem}';")
                sql = Q1_PG_SQL if q_type == "Q1" else Q2_PG_SQL
                cur.execute(sql)
                _ = cur.fetchall()
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return elapsed_ms, disk_spill, False
    except Exception as e:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return elapsed_ms, disk_spill, True

# ---------------------------------------------------------------------------
# Workers Concurrentes
# ---------------------------------------------------------------------------
def mongo_worker(client: pymongo.MongoClient, q_conf: dict, duration_s: float,
                 results: list, lock: threading.Lock) -> None:
    end_time = time.time() + duration_s
    while time.time() < end_time:
        lat_ms, err = exec_mongo_query(client, q_conf)
        with lock:
            results.append({"latency_ms": lat_ms, "error": err, "disk_spill": False})

def pg_worker(conn_str: str, q_conf: dict, duration_s: float,
              results: list, lock: threading.Lock) -> None:
    end_time = time.time() + duration_s
    while time.time() < end_time:
        lat_ms, disk_spill, err = exec_pg_query(conn_str, q_conf)
        with lock:
            results.append({"latency_ms": lat_ms, "error": err, "disk_spill": disk_spill})

# ---------------------------------------------------------------------------
# Ejecución de una celda de la matriz
# ---------------------------------------------------------------------------
def run_cell(engine: str, profile_name: str, profile: dict,
             query_id: str, query: dict, concurrency: int,
             run_number: int, csv_writer, csv_lock: threading.Lock,
             warmup_time: int, measure_time: int, cooldown_time: int) -> None:
    container = (
        profile["container_mongo"] if engine == "mongodb"
        else profile["container_pg"]
    )
    tag = f"[{engine}|{profile_name}|{query_id}|C{concurrency}|R{run_number}]"
    print(f"\n{tag} Iniciando celda...")

    # 1. Flush de caché
    flush_cache(container)

    # Inicializar clientes
    mongo_client = None
    pg_conn_str = f"host={PG_HOST} port={PG_PORT} dbname={PG_DB} user={PG_USER} password={PG_PASS}"
    if engine == "mongodb":
        mongo_client = pymongo.MongoClient(
            f"mongodb://{MONGO_HOST}:{MONGO_PORT}/",
            maxPoolSize=max(50, concurrency * 2)
        )

    try:
        # 2. Warmup
        if warmup_time > 0:
            print(f"{tag} -> Warmup {warmup_time}s...")
            warmup_res = []
            w_lock = threading.Lock()
            with ThreadPoolExecutor(max_workers=concurrency) as ex:
                futures = []
                for _ in range(concurrency):
                    if engine == "mongodb":
                        futures.append(ex.submit(mongo_worker, mongo_client, query, warmup_time, warmup_res, w_lock))
                    else:
                        futures.append(ex.submit(pg_worker, pg_conn_str, query, warmup_time, warmup_res, w_lock))
                for f in as_completed(futures):
                    pass

        # Métricas antes
        m_before = get_container_metrics(container)

        # 3. Medición
        print(f"{tag} -> Medición {measure_time}s con {concurrency} cliente(s)...")
        meas_res = []
        m_lock = threading.Lock()
        t_start = time.time()
        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            futures = []
            for _ in range(concurrency):
                if engine == "mongodb":
                    futures.append(ex.submit(mongo_worker, mongo_client, query, measure_time, meas_res, m_lock))
                else:
                    futures.append(ex.submit(pg_worker, pg_conn_str, query, measure_time, meas_res, m_lock))
            for f in as_completed(futures):
                pass
        t_end = time.time()
        elapsed_s = max(0.001, t_end - t_start)

        # 4. Cooldown
        if cooldown_time > 0:
            print(f"{tag} -> Cooldown {cooldown_time}s...")
            time.sleep(cooldown_time)

        # Métricas después
        m_after = get_container_metrics(container)

        # KPIs
        latencies = [r["latency_ms"] for r in meas_res if not r["error"]]
        errors = sum(1 for r in meas_res if r["error"])
        total_q = len(meas_res)

        if latencies:
            latencies.sort()
            n = len(latencies)
            p50 = latencies[int(n * 0.50)]
            p95 = latencies[int(n * 0.95)]
            p99 = latencies[int(n * 0.99)]
            qps = total_q / elapsed_s
        else:
            p50 = p95 = p99 = qps = 0.0

        disk_spill = any(r.get("disk_spill", False) for r in meas_res)
        cpu_throttled = max(0.0, m_after["cpu_throttled_seconds"] - m_before["cpu_throttled_seconds"])
        max_rss = m_after["max_rss_mb"]

        oom_killed = (errors > 0 and engine == "mongodb" and not query.get("allow_disk_use", True))
        status = "ok" if errors == 0 else ("oom" if oom_killed else "error")

        print(f"{tag} QPS={qps:.2f} | P50={p50:.1f}ms | P95={p95:.1f}ms | P99={p99:.1f}ms | "
              f"Throttled={cpu_throttled:.2f}s | RSS={max_rss:.1f}MB | disk_spill={disk_spill} | status={status}")

        row = {
            "timestamp_epoch":       int(t_start),
            "engine":                engine,
            "profile":               profile_name,
            "query_id":              query_id,
            "concurrency":           concurrency,
            "run_number":            run_number,
            "throughput_qps":        round(qps, 4),
            "latency_p50_ms":        round(p50, 2),
            "latency_p95_ms":        round(p95, 2),
            "latency_p99_ms":        round(p99, 2),
            "cpu_throttled_seconds": round(cpu_throttled, 4),
            "max_rss_mb":            round(max_rss, 2),
            "disk_spill":            disk_spill,
            "status":                status,
        }
        with csv_lock:
            csv_writer.writerow(row)

    finally:
        if mongo_client:
            mongo_client.close()

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark Runner - BD en la Nube 2026")
    parser.add_argument("--profile", choices=["baseline", "medium", "severe", "all"], default="baseline")
    parser.add_argument("--out-dir", default="./results")
    parser.add_argument("--warmup", type=int, default=WARMUP_S, help=f"Warmup seconds (default {WARMUP_S})")
    parser.add_argument("--measure", type=int, default=MEASURE_S, help=f"Measure seconds (default {MEASURE_S})")
    parser.add_argument("--cooldown", type=int, default=COOLDOWN_S, help=f"Cooldown seconds (default {COOLDOWN_S})")
    parser.add_argument("--concurrencies", nargs="+", type=int, default=CONCURRENCIES)
    parser.add_argument("--repetitions", type=int, default=REPETITIONS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.profile == "all":
        selected_profiles = list(PROFILES.items())
    elif args.profile == "baseline":
        selected_profiles = [("PROFILE_A_BASELINE", PROFILES["PROFILE_A_BASELINE"])]
    elif args.profile == "medium":
        selected_profiles = [("PROFILE_B_MEDIUM", PROFILES["PROFILE_B_MEDIUM"])]
    else:
        selected_profiles = [("PROFILE_C_SEVERE", PROFILES["PROFILE_C_SEVERE"])]

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "benchmark_results.csv"

    concs = args.concurrencies
    reps = args.repetitions

    total_cells = len(ENGINES) * len(selected_profiles) * len(QUERIES) * len(concs) * reps

    print("=" * 70)
    print("  Benchmark Runner (Native Drivers) - Bases de Datos en la Nube 2026")
    print("=" * 70)
    print(f"  Motores       : {', '.join(ENGINES)}")
    print(f"  Perfiles      : {', '.join(p for p, _ in selected_profiles)}")
    print(f"  Queries       : {', '.join(QUERIES.keys())}")
    print(f"  Concurrencias : {concs}")
    print(f"  Repeticiones  : {reps}")
    print(f"  Total celdas  : {total_cells}")
    print(f"  Tiempos (s)   : warmup={args.warmup}, measure={args.measure}, cooldown={args.cooldown}")
    print(f"  Salida CSV    : {csv_path}")
    print("=" * 70)

    if args.dry_run:
        cell = 0
        for engine in ENGINES:
            for p_name, _ in selected_profiles:
                for q_id in QUERIES:
                    for conc in concs:
                        for rep in range(1, reps + 1):
                            cell += 1
                            print(f"  [{cell:03d}] {engine} | {p_name} | {q_id} | C={conc} | R={rep}")
        return

    csv_lock = threading.Lock()
    file_exists = csv_path.exists() and csv_path.stat().st_size > 0
    completed_keys = set()
    if file_exists:
        try:
            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    completed_keys.add((
                        r.get("engine"),
                        r.get("profile"),
                        r.get("query_id"),
                        int(r.get("concurrency", 0)),
                        int(r.get("run_number", 0))
                    ))
            if completed_keys:
                print(f"[RESUME] Se detectaron {len(completed_keys)} celdas previamente completadas en el CSV. Se omitirán.")
        except Exception as e:
            print(f"[WARN] No se pudo leer progreso previo del CSV: {e}")

    csv_file = open(csv_path, "a" if file_exists else "w", newline="", encoding="utf-8")
    writer = csv.DictWriter(csv_file, fieldnames=CSV_HEADER)
    if not file_exists:
        writer.writeheader()

    cell_num = 0
    try:
        for engine in ENGINES:
            for p_name, profile in selected_profiles:
                for q_id, query in QUERIES.items():
                    for conc in concs:
                        for rep in range(1, reps + 1):
                            cell_num += 1
                            key = (engine, p_name, q_id, conc, rep)
                            if key in completed_keys:
                                print(f"  [CELDA {cell_num}/{total_cells} OMITIDA - YA COMPLETADA] {engine} | {p_name} | {q_id} | C={conc} | R={rep}")
                                continue

                            print(f"\n{'='*70}")
                            print(f"  Celda {cell_num}/{total_cells}")
                            run_cell(
                                engine=engine,
                                profile_name=p_name,
                                profile=profile,
                                query_id=q_id,
                                query=query,
                                concurrency=conc,
                                run_number=rep,
                                csv_writer=writer,
                                csv_lock=csv_lock,
                                warmup_time=args.warmup,
                                measure_time=args.measure,
                                cooldown_time=args.cooldown
                            )
                            csv_file.flush()
    except KeyboardInterrupt:
        print("\n[WARN] Benchmark interrumpido por el usuario.")
    finally:
        csv_file.close()

    print(f"\n[OK] Benchmark completado. Resultados en: {csv_path}")

if __name__ == "__main__":
    main()
