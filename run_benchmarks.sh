#!/usr/bin/env bash
# =============================================================================
# run_benchmarks.sh
# Automatización completa de la matriz de mediciones
# IT2iD - Bases de Datos en la Nube 2026
#
# Ejecuta los 3 perfiles en secuencia:
#   PROFILE_A_BASELINE -> PROFILE_B_MEDIUM -> PROFILE_C_SEVERE
#
# Para cada perfil:
#   1. Levanta los contenedores Docker del perfil
#   2. Carga el dataset en MongoDB y PostgreSQL (si no existe)
#   3. Ejecuta benchmark_runner.py
#   4. Baja los contenedores
#
# Uso:
#   bash run_benchmarks.sh [--profile baseline|medium|severe|all]
#   bash run_benchmarks.sh --skip-load    (si el dataset ya está cargado)
#   bash run_benchmarks.sh --dry-run      (solo imprime la matriz)
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Colores para output
# ---------------------------------------------------------------------------
RED='\033[0;31m';  GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m';  NC='\033[0m'

log_info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
log_ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_err()   { echo -e "${RED}[ERROR]${NC} $*"; }
log_step()  { echo -e "\n${BLUE}══════════════════════════════════════════════════${NC}"; \
              echo -e "${BLUE}  $*${NC}"; \
              echo -e "${BLUE}══════════════════════════════════════════════════${NC}"; }

# ---------------------------------------------------------------------------
# Parámetros
# ---------------------------------------------------------------------------
PROFILE="all"
SKIP_LOAD=false
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)    PROFILE="$2"; shift 2 ;;
    --skip-load)  SKIP_LOAD=true; shift ;;
    --dry-run)    DRY_RUN=true; shift ;;
    *)            log_err "Argumento desconocido: $1"; exit 1 ;;
  esac
done

# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${SCRIPT_DIR}/data"
RESULTS_DIR="${SCRIPT_DIR}/results"
LOGS_DIR="${SCRIPT_DIR}/logs"

mkdir -p "${DATA_DIR}" "${RESULTS_DIR}" "${LOGS_DIR}"

# ---------------------------------------------------------------------------
# Configuración de conexión
# ---------------------------------------------------------------------------
MONGO_HOST="${MONGO_HOST:-localhost}"
MONGO_PORT="${MONGO_PORT:-27017}"
MONGO_DB="${MONGO_DB:-benchmark}"

PG_HOST="${PG_HOST:-localhost}"
PG_PORT="${PG_PORT:-5432}"
PG_DB="${PG_DB:-benchmark}"
PG_USER="${PG_USER:-bench_user}"
PG_PASS="${PG_PASS:-bench_pass}"

export PGPASSWORD="${PG_PASS}"

# Tiempo de espera para que los contenedores estén listos
CONTAINER_READY_WAIT=30

# ---------------------------------------------------------------------------
# Funciones de Docker
# ---------------------------------------------------------------------------
docker_up() {
  local profile="$1"
  log_info "Levantando contenedores del perfil: ${profile}"
  docker compose --profile "${profile}" up -d
  log_info "Esperando ${CONTAINER_READY_WAIT}s para que los servicios estén listos..."
  sleep "${CONTAINER_READY_WAIT}"
}

docker_down() {
  local profile="$1"
  log_info "Bajando contenedores del perfil: ${profile}"
  docker compose --profile "${profile}" down --volumes --remove-orphans || true
}

wait_for_mongo() {
  log_info "Esperando a MongoDB..."
  local attempts=0
  until mongosh "mongodb://${MONGO_HOST}:${MONGO_PORT}/${MONGO_DB}" \
        --quiet --eval "db.runCommand({ping:1}).ok" 2>/dev/null | grep -q "1"; do
    attempts=$((attempts + 1))
    if [[ $attempts -ge 30 ]]; then
      log_err "MongoDB no responde después de 30 intentos."
      exit 1
    fi
    sleep 2
  done
  log_ok "MongoDB listo."
}

wait_for_postgres() {
  log_info "Esperando a PostgreSQL..."
  local attempts=0
  until psql "postgresql://${PG_USER}:${PG_PASS}@${PG_HOST}:${PG_PORT}/${PG_DB}" \
        -c "SELECT 1" -q >/dev/null 2>&1; do
    attempts=$((attempts + 1))
    if [[ $attempts -ge 30 ]]; then
      log_err "PostgreSQL no responde después de 30 intentos."
      exit 1
    fi
    sleep 2
  done
  log_ok "PostgreSQL listo."
}

# ---------------------------------------------------------------------------
# Generación y carga del dataset
# ---------------------------------------------------------------------------
generate_dataset() {
  if [[ -f "${DATA_DIR}/orders.jsonl" ]]; then
    log_warn "Dataset ya existe en ${DATA_DIR}/orders.jsonl — omitiendo generación."
    log_warn "Usa --skip-load para omitir también la carga, o elimina ${DATA_DIR} para regenerar."
    return 0
  fi

  log_step "Generando dataset sintético (500 000 registros)..."
  python3 "${SCRIPT_DIR}/synthetic_data_generator.py" \
    --records 500000 \
    --out-dir "${DATA_DIR}"
  log_ok "Dataset generado."
}

load_mongo() {
  log_step "Cargando dataset en MongoDB..."
  mongoimport \
    --host "${MONGO_HOST}:${MONGO_PORT}" \
    --db "${MONGO_DB}" \
    --collection orders \
    --file "${DATA_DIR}/orders.jsonl" \
    --jsonArray=false \
    --drop \
    --numInsertionWorkers 4
  log_ok "MongoDB: ${MONGO_DB}.orders cargado."
}

load_postgres() {
  log_step "Cargando dataset en PostgreSQL..."
  # Truncar tabla antes de cargar
  psql "postgresql://${PG_USER}:${PG_PASS}@${PG_HOST}:${PG_PORT}/${PG_DB}" \
    -c "TRUNCATE TABLE orders;" -q

  # Cargar con COPY (mucho más rápido que INSERT)
  psql "postgresql://${PG_USER}:${PG_PASS}@${PG_HOST}:${PG_PORT}/${PG_DB}" \
    -c "\COPY orders(id,data) FROM '${DATA_DIR}/orders_pg.csv' CSV HEADER" -q

  # ANALYZE para que el planificador tenga estadísticas actualizadas
  psql "postgresql://${PG_USER}:${PG_PASS}@${PG_HOST}:${PG_PORT}/${PG_DB}" \
    -c "ANALYZE orders;" -q

  log_ok "PostgreSQL: tabla orders cargada y analizada."
}

# ---------------------------------------------------------------------------
# Ejecución de benchmark para un perfil
# ---------------------------------------------------------------------------
run_profile() {
  local profile_flag="$1"    # baseline | medium | severe
  local profile_id="$2"      # PROFILE_A_BASELINE | ...

  log_step "PERFIL: ${profile_id}"

  # Subir contenedores
  docker_up "${profile_flag}"

  # Esperar servicios
  wait_for_mongo
  wait_for_postgres

  # Cargar dataset (si no se omite)
  if [[ "${SKIP_LOAD}" == "false" ]]; then
    generate_dataset
    load_mongo
    load_postgres
  else
    log_warn "Carga del dataset omitida (--skip-load)."
  fi

  # Ejecutar benchmark runner
  log_step "Ejecutando benchmark_runner.py — Perfil: ${profile_flag}"
  local log_file="${LOGS_DIR}/bench_${profile_flag}_$(date +%Y%m%d_%H%M%S).log"

  if [[ "${DRY_RUN}" == "true" ]]; then
    python3 "${SCRIPT_DIR}/benchmark_runner.py" \
      --profile "${profile_flag}" \
      --out-dir "${RESULTS_DIR}" \
      --dry-run 2>&1 | tee "${log_file}"
  else
    python3 "${SCRIPT_DIR}/benchmark_runner.py" \
      --profile "${profile_flag}" \
      --out-dir "${RESULTS_DIR}" \
      2>&1 | tee "${log_file}"
  fi

  log_ok "Benchmark del perfil ${profile_id} completado. Log: ${log_file}"

  # Bajar contenedores antes del siguiente perfil
  docker_down "${profile_flag}"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
log_step "Benchmark BD en la Nube 2026 — IT2iD"
log_info "Perfil seleccionado : ${PROFILE}"
log_info "Skip dataset load   : ${SKIP_LOAD}"
log_info "Dry run             : ${DRY_RUN}"
log_info "Resultados en       : ${RESULTS_DIR}"

START_TIME=$(date +%s)

case "${PROFILE}" in
  baseline)
    run_profile "baseline" "PROFILE_A_BASELINE"
    ;;
  medium)
    run_profile "medium" "PROFILE_B_MEDIUM"
    ;;
  severe)
    run_profile "severe" "PROFILE_C_SEVERE"
    ;;
  all)
    run_profile "baseline" "PROFILE_A_BASELINE"
    run_profile "medium"   "PROFILE_B_MEDIUM"
    run_profile "severe"   "PROFILE_C_SEVERE"
    ;;
  *)
    log_err "Perfil desconocido: ${PROFILE}. Usa: baseline | medium | severe | all"
    exit 1
    ;;
esac

END_TIME=$(date +%s)
ELAPSED=$(( END_TIME - START_TIME ))
log_step "Benchmark COMPLETADO"
log_ok "Tiempo total: $(( ELAPSED / 3600 ))h $(( (ELAPSED % 3600) / 60 ))m $(( ELAPSED % 60 ))s"
log_ok "CSV de resultados: ${RESULTS_DIR}/benchmark_results.csv"
log_info "Siguiente paso: python3 scripts/analyze_results.py"

