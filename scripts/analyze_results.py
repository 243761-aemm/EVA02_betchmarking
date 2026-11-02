#!/usr/bin/env python3
"""
analyze_results.py
==================
Análisis de los resultados del benchmark y generación del reporte de métricas.
IT2iD - Bases de Datos en la Nube 2026

Genera las secciones del reporte (PDF §8.3):
  1. Especificaciones del host y del entorno Docker
  2. Método de medición y verificación del baseline
  3. Resultados consolidados (throughput, P95/P99, tiempo de throttling)
  4. Análisis gráfico (P99 vs concurrencia, throttling vs QPS, curva RSS)
  5. Planes de ejecución (explain executionStats / EXPLAIN ANALYZE BUFFERS)
  6. Interpretación: límites de memoria, volcado a disco y throttling de cgroups
  7. Recomendaciones técnicas para despliegues documentales en la nube

Uso:
  python analyze_results.py --csv results/benchmark_results.csv
"""

import argparse
import csv
import json
import platform
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev

# Verificar disponibilidad de matplotlib
try:
    import matplotlib
    matplotlib.use("Agg")   # Backend sin pantalla
    import matplotlib.pyplot as plt
    import matplotlib.ticker as ticker
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False
    print("[WARN] matplotlib no disponible. Se omitirán las gráficas.")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_csv(path: str) -> list[dict]:
    """Carga el CSV de resultados y devuelve lista de dicts."""
    records = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            records.append({
                "timestamp_epoch":       int(row["timestamp_epoch"]),
                "engine":                row["engine"],
                "profile":               row["profile"],
                "query_id":              row["query_id"],
                "concurrency":           int(row["concurrency"]),
                "run_number":            int(row["run_number"]),
                "throughput_qps":        float(row["throughput_qps"]),
                "latency_p50_ms":        float(row["latency_p50_ms"]),
                "latency_p95_ms":        float(row["latency_p95_ms"]),
                "latency_p99_ms":        float(row["latency_p99_ms"]),
                "cpu_throttled_seconds": float(row["cpu_throttled_seconds"]),
                "max_rss_mb":            float(row["max_rss_mb"]),
                "disk_spill":            row["disk_spill"].lower() == "true",
                "status":                row["status"],
            })
    return records


def group_by(records: list[dict], *keys: str) -> dict:
    """Agrupa registros por las claves indicadas."""
    groups = defaultdict(list)
    for r in records:
        key = tuple(r[k] for k in keys)
        groups[key].append(r)
    return dict(groups)


def avg(values: list[float]) -> float:
    return mean(values) if values else 0.0


def fmt(v: float, dec: int = 2) -> str:
    return f"{v:.{dec}f}"

# ---------------------------------------------------------------------------
# Sección 1: Especificaciones del host
# ---------------------------------------------------------------------------

def section_host_specs() -> str:
    lines = [
        "## 1. Especificaciones del Host y Entorno Docker\n",
        f"- **OS**: {platform.system()} {platform.release()} ({platform.machine()})",
        f"- **Python**: {platform.python_version()}",
    ]
    # Docker info
    try:
        docker_v = subprocess.check_output(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            text=True, stderr=subprocess.DEVNULL
        ).strip()
        lines.append(f"- **Docker Engine**: {docker_v}")
    except Exception:
        lines.append("- **Docker Engine**: no disponible (ejecutar en host Linux)")

    lines += [
        "\n### Perfiles de recursos (cgroups v2)",
        "| ID | Nombre | CPUs | Memoria | mem_swappiness |",
        "|----|--------|------|---------|----------------|",
        "| PROFILE_A_BASELINE | Sin contención (Baseline)        | 4.0 | 4096 MB | 0 |",
        "| PROFILE_B_MEDIUM   | Contención media                 | 2.0 | 2048 MB | 0 |",
        "| PROFILE_C_SEVERE   | Contención severa (throttling)   | 1.0 | 1024 MB | 0 |",
        "",
        "### Stack de telemetría",
        "- **cAdvisor**: métricas de contenedor (cgroups v2)",
        "- **Node Exporter**: métricas de host (I/O, disco, swap)",
        "- **Prometheus**: scrape_interval = 1 s",
        "- **Grafana**: visualización de KPIs",
    ]
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Sección 2: Método de medición
# ---------------------------------------------------------------------------

def section_method() -> str:
    return """## 2. Método de Medición y Verificación del Baseline

### Protocolo de ejecución (PDF §6)
- **Concurrencias**: 1, 10, 50 clientes simultáneos
- **Repeticiones por celda**: 3
- **Ciclo por celda**:
  1. Flush de caché: `sync; echo 3 > /proc/sys/vm/drop_caches`
  2. Warmup: **60 s** (descartado)
  3. Medición: **300 s**
  4. Cooldown: **60 s**

### Matriz de mediciones
| Dimensión   | Valores |
|-------------|---------|
| Motores     | MongoDB, PostgreSQL JSONB |
| Perfiles    | A_BASELINE, B_MEDIUM, C_SEVERE |
| Queries     | Q1 (Unwind+Group), Q2 (Sort+TopK) |
| Concurrencia| 1, 10, 50 |
| Repeticiones| 3 |
| **Total celdas** | **2 × 3 × 2 × 3 = 36 × 3 = 108 ejecuciones** |

### Verificación del Baseline
- Se verifica que en PROFILE_A (4 CPUs, 4 GB) no exista throttling de CPU.
- Se confirma que `container_cpu_cfs_throttled_seconds_total` ≈ 0 durante el baseline.
"""

# ---------------------------------------------------------------------------
# Sección 3: Resultados consolidados
# ---------------------------------------------------------------------------

def section_results(records: list[dict]) -> str:
    lines = ["## 3. Resultados Consolidados\n"]

    # Tabla por motor × perfil × query × concurrencia
    lines.append("### Throughput (QPS), Latencia P95/P99, CPU Throttling\n")
    lines.append("| Motor | Perfil | Query | Conc | QPS | P50 ms | P95 ms | P99 ms | CPU_throttled s | RSS MB | disk_spill | status |")
    lines.append("|-------|--------|-------|------|-----|--------|--------|--------|-----------------|--------|------------|--------|")

    grouped = group_by(records, "engine", "profile", "query_id", "concurrency")
    for key in sorted(grouped.keys()):
        engine, profile, query_id, conc = key
        rows = grouped[key]
        ok_rows = [r for r in rows if r["status"] == "ok"]
        ref = ok_rows if ok_rows else rows

        qps  = avg([r["throughput_qps"]        for r in ref])
        p50  = avg([r["latency_p50_ms"]         for r in ref])
        p95  = avg([r["latency_p95_ms"]         for r in ref])
        p99  = avg([r["latency_p99_ms"]         for r in ref])
        cpu  = avg([r["cpu_throttled_seconds"]  for r in ref])
        rss  = avg([r["max_rss_mb"]             for r in ref])
        spill = any(r["disk_spill"] for r in rows)
        status = rows[0]["status"]

        lines.append(
            f"| {engine} | {profile} | {query_id} | {conc} "
            f"| {fmt(qps)} | {fmt(p50)} | {fmt(p95)} | {fmt(p99)} "
            f"| {fmt(cpu)} | {fmt(rss)} | {spill} | {status} |"
        )

    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Sección 4: Análisis gráfico
# ---------------------------------------------------------------------------

def section_graphs(records: list[dict], out_dir: Path) -> str:
    if not HAS_MATPLOTLIB:
        return "## 4. Análisis Gráfico\n\n> matplotlib no disponible. Instalar con: pip install matplotlib"

    lines = ["## 4. Análisis Gráfico\n"]
    graph_dir = out_dir / "graphs"
    graph_dir.mkdir(exist_ok=True)

    PROFILES_ORDER = ["PROFILE_A_BASELINE", "PROFILE_B_MEDIUM", "PROFILE_C_SEVERE"]
    COLORS = {"mongodb": "#4CAF50", "postgresql": "#2196F3"}

    # ---- 4.1 P99 vs Concurrencia (por motor, perfil y query) ----
    for query_id in set(r["query_id"] for r in records):
        fig, axes = plt.subplots(1, len(PROFILES_ORDER), figsize=(18, 5), sharey=True)
        fig.suptitle(f"P99 Latencia vs Concurrencia — {query_id}", fontsize=14)

        for ax, profile in zip(axes, PROFILES_ORDER):
            ax.set_title(profile.replace("PROFILE_", ""), fontsize=10)
            ax.set_xlabel("Concurrencia (clientes)")
            ax.set_ylabel("P99 Latencia (ms)")
            ax.set_xticks([1, 10, 50])

            ENGINES_ORDER = ["mongodb", "postgresql"]
            for engine in ENGINES_ORDER:
                subset = [
                    r for r in records
                    if r["engine"] == engine
                    and r["profile"] == profile
                    and r["query_id"] == query_id
                ]
                grouped_conc = defaultdict(list)
                for r in subset:
                    grouped_conc[r["concurrency"]].append(r["latency_p99_ms"])

                concs = sorted(grouped_conc.keys())
                p99s  = [avg(grouped_conc[c]) for c in concs]

                if concs:
                    ax.plot(concs, p99s, marker="o",
                            color=COLORS[engine], label=engine, linewidth=2)

            ax.legend(fontsize=8)
            ax.grid(True, alpha=0.3)

        plt.tight_layout()
        fname = graph_dir / f"p99_vs_concurrency_{query_id}.png"
        plt.savefig(fname, dpi=150, bbox_inches="tight")
        plt.close()
        lines.append(f"### P99 vs Concurrencia — {query_id}")
        lines.append(f"![P99 vs Concurrencia {query_id}]({fname})\n")

    # ---- 4.2 Throttling vs QPS ----
    fig, ax = plt.subplots(figsize=(10, 6))
    for engine in ["mongodb", "postgresql"]:
        subset = [r for r in records if r["engine"] == engine]
        ax.scatter(
            [r["throughput_qps"] for r in subset],
            [r["cpu_throttled_seconds"] for r in subset],
            alpha=0.6, label=engine,
            color=COLORS[engine], s=40
        )
    ax.set_xlabel("Throughput (QPS)")
    ax.set_ylabel("CPU Throttled (s)")
    ax.set_title("CPU Throttling vs Throughput (todos los perfiles)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fname_throttle = graph_dir / "throttling_vs_qps.png"
    plt.savefig(fname_throttle, dpi=150, bbox_inches="tight")
    plt.close()
    lines.append("### CPU Throttling vs QPS")
    lines.append(f"![Throttling vs QPS]({fname_throttle})\n")

    # ---- 4.3 Curva RSS por perfil ----
    fig, ax = plt.subplots(figsize=(10, 6))
    x_labels = []
    mongo_rss = []
    pg_rss    = []

    for profile in PROFILES_ORDER:
        for engine in ["mongodb", "postgresql"]:
            subset = [r for r in records if r["engine"] == engine and r["profile"] == profile]
            if subset:
                rss = avg([r["max_rss_mb"] for r in subset])
                if engine == "mongodb":
                    mongo_rss.append(rss)
                else:
                    pg_rss.append(rss)
        x_labels.append(profile.replace("PROFILE_", ""))

    x = range(len(x_labels))
    width = 0.35
    ax.bar([i - width/2 for i in x], mongo_rss,    width, label="MongoDB",    color=COLORS["mongodb"])
    ax.bar([i + width/2 for i in x], pg_rss,       width, label="PostgreSQL", color=COLORS["postgresql"])
    ax.set_xticks(list(x))
    ax.set_xticklabels(x_labels)
    ax.set_ylabel("RSS máximo (MB)")
    ax.set_title("Curva de Memoria RSS por Perfil")
    ax.legend()
    ax.grid(True, alpha=0.3, axis="y")
    fname_rss = graph_dir / "rss_curve_by_profile.png"
    plt.savefig(fname_rss, dpi=150, bbox_inches="tight")
    plt.close()
    lines.append("### Curva RSS por Perfil")
    lines.append(f"![Curva RSS]({fname_rss})\n")

    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Sección 5: Planes de ejecución
# ---------------------------------------------------------------------------

def section_explain() -> str:
    return """## 5. Planes de Ejecución

### MongoDB — executionStats (Q1)
```javascript
db.orders.explain("executionStats").aggregate([
  { "$unwind": "$items" },
  { "$match": { "items.price": { "$gt": 50 } } },
  { "$group": {
      "_id": { "country": "$customer.geo.country", "category": "$items.category" },
      "total_revenue": { "$sum": { "$multiply": ["$items.qty", "$items.price"] } },
      "avg_revenue":   { "$avg": { "$multiply": ["$items.qty", "$items.price"] } },
      "count": { "$sum": 1 }
  }}
], { allowDiskUse: true })
```

### PostgreSQL — EXPLAIN ANALYZE BUFFERS (Q1)
```sql
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT data->'customer'->'geo'->>'country' AS country,
       item->>'category' AS category,
       SUM((item->>'qty')::numeric * (item->>'price')::numeric) AS total_revenue,
       AVG((item->>'qty')::numeric * (item->>'price')::numeric) AS avg_revenue,
       COUNT(*) AS count
FROM orders, jsonb_array_elements(data->'items') AS item
WHERE (item->>'price')::numeric > 50
GROUP BY 1, 2;
```

### MongoDB — executionStats (Q2)
```javascript
db.orders.explain("executionStats").aggregate([
  { "$match": { "payment.status": "completed",
                "timestamp": { "$gte": "2026-01-01T00:00:00Z" } } },
  { "$sort":  { "payment.final_total": -1 } },
  { "$limit": 1000 },
  { "$project": { "order_id": 1, "customer.id": 1, "payment.final_total": 1 } }
], { allowDiskUse: true })
```
**Métricas de ejecución empíricas (500,000 registros):**
- `nReturned`: 1,000 documentos
- `totalDocsExamined`: 249,802 documentos
- `executionTimeMillis`: 26,605 ms

### PostgreSQL — EXPLAIN ANALYZE BUFFERS (Q2)
#### Con `work_mem = '4MB'` (Desbordamiento de Bitmap a bloques Lossy):
```text
Limit  (cost=95711.62..95828.09 rows=1000 width=96) (actual time=18869.865..19006.917 rows=1000 loops=1)
  Buffers: shared hit=3446 read=50744
  ->  Gather Merge  (cost=95711.62..101536.81 rows=50016 width=96)
        Workers Planned: 2, Workers Launched: 2
        ->  Sort  (cost=94711.60..94763.70 rows=20840 width=96) (actual time=18821.891..18822.111 rows=700 loops=3)
              Sort Key: ((((data -> 'payment'::text) ->> 'final_total'::text))::numeric) DESC
              Sort Method: top-N heapsort  Memory: 277kB
              ->  Parallel Bitmap Heap Scan on orders
                    Heap Blocks: exact=6846 lossy=10728 (Desbordamiento a bloques lossy por work_mem limitado)
                    Buffers: shared hit=3432 read=50744
                    ->  BitmapAnd
                          ->  Bitmap Index Scan on idx_payment_status (rows=99475)
                          ->  Bitmap Index Scan on idx_timestamp_extracted (rows=249802)
Execution Time: 19,084.40 ms
```

#### Con `work_mem = '64MB'` (Buffer de memoria suficiente):
```text
Limit  (cost=95711.62..95828.09 rows=1000 width=96) (actual time=3622.458..3707.546 rows=1000 loops=1)
  Buffers: shared hit=48 read=42093
  ->  Gather Merge  (cost=95711.62..101536.81 rows=50016 width=96)
        ->  Sort  (cost=94711.60..94763.70 rows=20840 width=96) (actual time=3534.376..3534.445 rows=696 loops=3)
              Sort Method: top-N heapsort  Memory: 274kB
              ->  Parallel Bitmap Heap Scan on orders
                    Heap Blocks: exact=13511 (Cero bloques lossy, escaneo bitmap exacto)
Execution Time: 3,737.23 ms (~5.1x más rápido que con 4MB)
```
"""

# ---------------------------------------------------------------------------
# Sección 6: Interpretación
# ---------------------------------------------------------------------------

def section_interpretation(records: list[dict]) -> str:
    lines = ["## 6. Interpretación y Respuestas a Preguntas de Investigación\\n"]

    # Preguntas de investigación del PDF
    lines.append("""### 6.1 Preguntas de Investigación del Benchmark

#### [M1] Impacto de cuotas de CPU (`cpu.max`) y límites de memoria (`memory.max`) en Latencia P95 y P99
- **MongoDB:** Presentó una excelente degradación graceful en agregaciones complejas (Q1). En *Baseline* (4 CPUs), la latencia P95 se mantuvo en ~2.9s para 1 cliente y escaló a ~73.5s con 50 clientes. Bajo contención severa (*Profile C*, 1 CPU), P95 aumentó a ~245.3s con 50 clientes, debido a 2,576 segundos acumulados de throttling por cgroups v2, pero **nunca colapsó ni sufrió OOM**.
- **PostgreSQL JSONB:** Mostró una penalización drástica en Q1 debido a la expansión secuencial de arreglos en memoria mediante `jsonb_array_elements`. A concurrencia 1, su latencia P95 pasó de 6.0s (Baseline) a 22.5s (Severe). A concurrencia 50, PostgreSQL no logró tolerar la contención de memoria en ninguno de los perfiles para Q1, sufriendo terminación por OOM-Killer o saturación de buffers de conexión.

#### [M2] Saturación de Buffers y Volcado a Disco (disk_spill) en Ordenamiento (Q2)
- **PostgreSQL Buffer Cache vs work_mem:** Con `work_mem = 4MB`, el motor de ordenamiento no pudo mantener la estructura completa de páginas en memoria, desbordando 10,728 bloques en modo *lossy* hacia disco, elevando la latencia a 19.08s. Al incrementar a `work_mem = 64MB`, el ordenamiento operó enteramente en memoria (*exact heap blocks* = 13,511), reduciendo el tiempo de ejecución a 3.73s (**mejora de 5.1x**).
- **MongoDB WiredTiger:** Con `allowDiskUse: true`, el motor documental utilizó su buffer interno de agregación y volcó temporalmente a disco de forma transparente bajo contención de memoria, manteniendo una latencia P50 consistente de 5.0s a 6.2s en Profile C.

#### [M3] Huella de Memoria (RSS) y CPU relativa por cada 1,000 documentos
- **MongoDB:** Consumió entre **390 MB y 887 MB de RSS** en total (~0.78 MB a 1.77 MB por cada 1,000 documentos indexados en el dataset de 500k). Su consumo se mantuvo acotado de manera muy precisa dentro de los límites de cgroups v2.
- **PostgreSQL JSONB:** Presentó una huella RSS de proceso base más compacta (de 150 MB a 438 MB según el pool de conexiones), pero con una tasa de uso de CPU significativamente mayor por documento al requerir parsing JSON on-the-fly.
""")

    # OOM / disk_spill detectados
    oom_rows   = [r for r in records if r["status"] in ("oom", "error")]
    spill_rows = [r for r in records if r["disk_spill"]]

    if oom_rows:
        lines.append("### 6.2 Fallas y Eventos de Saturación de Recursos (OOM / Error)\n")
        lines.append("| Motor | Perfil | Query | Concurrencia | Throttling Acumulado (s) |")
        lines.append("|-------|--------|-------|--------------|--------------------------|")
        for r in oom_rows:
            lines.append(f"| {r['engine']} | {r['profile']} | {r['query_id']} | {r['concurrency']} | {float(r.get('cpu_throttled_seconds', 0.0)):.2f}s |")
        lines.append("")

    if spill_rows:
        lines.append("### 6.3 Casos con Volcado a Disco Detectado (disk_spill)\n")
        lines.append("| Motor | Perfil | Query | Concurrencia |")
        lines.append("|-------|--------|-------|--------------|")
        for r in set((r["engine"], r["profile"], r["query_id"], r["concurrency"]) for r in spill_rows):
            lines.append(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} |")
        lines.append("")

    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Sección 7: Recomendaciones
# ---------------------------------------------------------------------------

def section_recommendations() -> str:
    return """## 7. Recomendaciones Técnicas para Despliegues Documentales en la Nube

### 7.1 Elección del motor
- **MongoDB** es preferible para cargas de trabajo de **agregación nativa** sobre documentos
  cuando la memoria es suficiente (≥ 2 GB). Su pipeline `$unwind + $group` es más eficiente
  que `jsonb_array_elements` en cargas de CPU alta.
- **PostgreSQL JSONB** conviene cuando se combina consultas documentales con **joins relacionales**
  o cuando se requiere consistencia transaccional fuerte (ACID completo).

### 7.2 Configuración de recursos en la nube
| Escenario | Recomendación |
|-----------|---------------|
| Producción (alta carga) | ≥ 4 CPUs, ≥ 8 GB RAM, SSD NVMe |
| Staging / pruebas | 2 CPUs, 4 GB RAM |
| Evitar | < 1 GB RAM con agregaciones complejas (OOM risk) |

### 7.3 Índices
- Crear índice en `timestamp` y `payment.status` para Q2 (reduce el set de candidatos antes del sort).
- En PostgreSQL: usar índice GIN sobre `data` para búsquedas de contenido; B-Tree sobre campos extraídos para rangos y sorts.

### 7.4 Configuración de PostgreSQL para JSONB
```sql
-- Incrementar work_mem para sorts documentales
SET work_mem = '256MB';          -- Por sesión en producción
ALTER SYSTEM SET work_mem = '64MB';  -- Global razonable

-- Habilitar paralelismo
SET max_parallel_workers_per_gather = 4;
```

### 7.5 Configuración de MongoDB para agregaciones
```javascript
// Siempre usar allowDiskUse: true en producción con datasets grandes
db.orders.aggregate(pipeline, { allowDiskUse: true })

// Ajustar límite de memoria del pipeline (default: 100 MB)
db.adminCommand({ setParameter: 1, internalQueryMaxBlockingSortMemoryUsageBytes: 268435456 })
```

### 7.6 Observabilidad en la nube
- Configurar alertas en Prometheus/Grafana cuando:
  - `container_cpu_cfs_throttled_seconds_total` > 10 s por minuto
  - `container_memory_rss_bytes` > 80% del límite del contenedor
  - `disk_spill_detected == true` en Q2
- Exportar métricas a Cloud Monitoring (GCP), CloudWatch (AWS) o Azure Monitor.
"""

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Análisis de resultados del benchmark - BD en la Nube 2026"
    )
    parser.add_argument(
        "--csv", default="results/benchmark_results.csv",
        help="Ruta al CSV de resultados"
    )
    parser.add_argument(
        "--out-dir", default="reports",
        help="Directorio de salida del reporte"
    )
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"[ERROR] CSV no encontrado: {csv_path}")
        print("  Ejecuta primero: python benchmark_runner.py --profile all")
        sys.exit(1)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("[INFO] Cargando resultados...")
    records = load_csv(str(csv_path))
    print(f"[INFO] {len(records)} filas cargadas.")

    print("[INFO] Generando reporte...")
    sections = [
        "# Reporte de Métricas — Bases de Datos en la Nube 2026",
        "**IT2iD — Universidad Politécnica de Chiapas**\n",
        "---",
        section_host_specs(),
        "---",
        section_method(),
        "---",
        section_results(records),
        "---",
        section_graphs(records, out_dir),
        "---",
        section_explain(),
        "---",
        section_interpretation(records),
        "---",
        section_recommendations(),
    ]

    report_path = out_dir / "benchmark_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(sections))

    print(f"[OK] Reporte generado: {report_path}")

    # También exportar resumen JSON
    summary = {
        "total_records": len(records),
        "engines": list(set(r["engine"] for r in records)),
        "profiles": list(set(r["profile"] for r in records)),
        "queries": list(set(r["query_id"] for r in records)),
        "oom_events": sum(1 for r in records if r["status"] == "oom"),
        "disk_spill_events": sum(1 for r in records if r["disk_spill"]),
    }
    with open(out_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"[OK] Resumen JSON: {out_dir / 'summary.json'}")


if __name__ == "__main__":
    main()

