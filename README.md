# Benchmark: MongoDB vs PostgreSQL JSONB bajo Contención de Recursos (cgroups v2)

**Universidad Politécnica de Chiapas — UPChiapas**  
**Ingeniería en Tecnologías de la Información e Innovación Digital (IT2iD)**  
**Asignatura:** Bases de Datos en la Nube · Edición 2026  
**Protocolo Académico:** Playbook Oficial §6 (Protocolo Estricto — 162 ejecuciones)

---

## 📌 Resumen del Proyecto

Este repositorio contiene la implementación experimental, suite de pruebas automatizadas y resultados consolidados del estudio comparativo entre **MongoDB (v7.x, motor WiredTiger)** y **PostgreSQL (v16, tipo de dato estructurado JSONB)** bajo límites estrictos de CPU (*CFS throttling*) y memoria física mediante **Docker cgroups v2**.

Se evaluó un corpus sintético de **500,000 documentos semiestructurados** (1.2 GB de datos reales) bajo tres perfiles de contención progresiva, ejecutando **162 corridas completas** (3 repeticiones independientes por celda) con un ciclo estricto de:
* Invalidador de caché OS: `sync; echo 3 > /proc/sys/vm/drop_caches`
* **Warmup:** 60 segundos (telemetría descartada)
* **Medición:** 300 segundos (5 minutos en régimen permanente)
* **Cooldown:** 60 segundos (enfriamiento térmico)
* **Duración total:** ~10.52 horas de telemetría continua.

---

## 📄 Entregables Principales

| Entregable | Archivo / Ubicación | Descripción |
|---|---|---|
| **Reporte Completo en PDF** | [`Reporte_Benchmark_BD_2026.pdf`](Reporte_Benchmark_BD_2026.pdf) | Documento formal de 11 páginas con Agradecimientos, 7 secciones requeridas, tablas y 5 gráficas. |
| **Fuente en LaTeX** | [`Reporte_Benchmark_BD_2026.tex`](Reporte_Benchmark_BD_2026.tex) | Código fuente en LaTeX con tipografía académica y tablas estilizadas. |
| **Matriz CSV (162 filas)** | [`results/benchmark_results.csv`](results/benchmark_results.csv) | Dataset consolidado con las 14 columnas exigidas por la especificación §8.2. |
| **Gráficos de Análisis** | [`graphs/`](graphs/) | 5 figuras en alta resolución (P99 vs Concurrencia, Throttling vs QPS, Curva RSS). |

---

## 🏗️ Estructura del Repositorio (Cumplimiento Playbook §8.1)

```
Betchmarking-BD/
├── .gitignore                     # Configuración de exclusión (ignora reports/*.md y raw data masivo)
├── README.md                      # Documentación completa y guía de reproducción
├── Reporte_Benchmark_BD_2026.pdf  # Reporte ejecutivo final listo para presentación
├── Reporte_Benchmark_BD_2026.tex  # Código fuente LaTeX del reporte
├── docker-compose.yml             # Perfiles A (Baseline), B (Medium), C (Severe) con cgroups v2
├── synthetic_data_generator.py    # Generador de dataset (500k documentos de e-commerce)
├── benchmark_runner.py            # Runner de concurrencia nativo (pymongo + psycopg)
├── run_strict.py                  # Orquestador del protocolo estricto PDF §6 (162 celdas)
├── run_benchmarks.sh              # Script de automatización de benchmark
├── queries/                       # Consultas normalizadas para ambos motores
│   ├── q1_mongo.js                # Q1: $unwind + $match + $group
│   ├── q1_postgres.sql            # Q1: jsonb_array_elements + GROUP BY
│   ├── q2_mongo.js                # Q2: Sort + Top-1000 con allowDiskUse
│   └── q2_postgres.sql            # Q2: Order By DESC con work_mem parametrizado
├── results/
│   └── benchmark_results.csv      # 162 filas consolidadas (54 Baseline, 54 Medium, 54 Severe)
├── graphs/                        # Gráficas generadas para el análisis
│   ├── p99_vs_concurrency_Q1.png
│   ├── p99_vs_concurrency_Q2_disk_false.png
│   ├── p99_vs_concurrency_Q2_disk_true.png
│   ├── rss_curve_by_profile.png
│   └── throttling_vs_qps.png
└── scripts/
    ├── generate_pdf_report.py     # Generador automatizado del PDF formal (ReportLab)
    ├── analyze_results.py         # Analizador de métricas y generador de gráficos
    ├── get_explains.py            # Extractor empírico de planes EXPLAIN ANALYZE
    ├── mongo_init.js              # Inicialización e índices de MongoDB
    ├── pg_init.sql                # Inicialización e índices B-Tree/GIN de PostgreSQL
    └── prometheus.yml             # Configuración de scraping 1s para Prometheus
```

---

## ⚙️ Perfiles de Recursos Evaluados (cgroups v2)

| ID Perfil | Nombre | CPUs (CFS Quota) | Límite RAM (Hard) | Swappiness |
|---|---|---|---|---|
| **PROFILE_A_BASELINE** | Sin contención (Baseline) | 4.0 | 4,096 MB | 0 |
| **PROFILE_B_MEDIUM** | Contención media | 2.0 | 2,048 MB | 0 |
| **PROFILE_C_SEVERE** | Contención severa (throttling activo) | 1.0 | 1,024 MB | 0 |

---

## 🚀 Guía de Reproducción Rápida

### 1. Requisitos Previos
* Docker Engine 24+ y Docker Compose v2 (con soporte de cgroups v2 habilitado)
* Python 3.10+ con librerías: `pip install pymongo psycopg[binary] matplotlib reportlab pymupdf`

### 2. Generar Dataset de 500k Registros
```bash
python synthetic_data_generator.py --count 500000 --out-dir data
```

### 3. Ejecutar Matriz Completa de Pruebas (162 ejecuciones)
```bash
python run_strict.py
```

### 4. Regenerar Reporte y Gráficas
```bash
python scripts/analyze_results.py --csv results/benchmark_results.csv --out-dir reports
python scripts/generate_pdf_report.py
```

---

## 🏆 Hallazgos Clave

1. **Resiliencia Documental:** **MongoDB** mantuvo degradación elástica estable (*graceful degradation*) incluso bajo contención extrema (1 CPU / 1 GB RAM, acumulando 4,872s de throttling de CPU) sin registrar ninguna caída ni OOM-Killer.
2. **Colapso de PostgreSQL en Desanidamiento Concurrente:** En Q1 a C=50 clientes, **PostgreSQL colapsó sistemáticamente** con fallos de asignación/conexión debido al coste de desempaquetar arreglos en memoria mediante `jsonb_array_elements`.
3. **Optimización por `work_mem`:** En ordenamiento masivo Top-K (Q2), incrementar `work_mem` de 4 MB a 64 MB aceleró PostgreSQL en **5.1×** (de 19.08s a 3.73s) al eliminar 10,728 bloques en modo *lossy* en el Bitmap Heap Scan.
