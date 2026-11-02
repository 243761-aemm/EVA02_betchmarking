-- =============================================================================
-- q2_sort_topk.sql
-- Q2 — Massive Sort & Top-K (estrés de buffer)
-- IT2iD - Bases de Datos en la Nube 2026
--
-- Descripción (PDF §5.2):
--   Filtrar órdenes completadas en un rango de fechas, ordenar descendente
--   por payment.final_total y obtener el Top-1000 sin índices previos en el total.
--
-- Variantes de estrés de memoria (PDF):
--   SET work_mem = '4MB';   -- Fuerza volcado a disco temporal (disk_spill)
--   SET work_mem = '64MB';  -- Ordenamiento en memoria
--
-- El benchmark_runner ejecuta ambas variantes y registra disk_spill.
-- =============================================================================

-- ---- Variante A: work_mem pequeño -> disk spill esperado ----
-- SET work_mem = '4MB';

-- ---- Variante B: work_mem grande -> sort en memoria ----
-- SET work_mem = '64MB';

-- Para EXPLAIN ANALYZE (plan de ejecución con BUFFERS):
-- EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)

SELECT
    data->>'order_id'                               AS order_id,
    data->'customer'->>'id'                         AS customer_id,
    (data->'payment'->>'final_total')::numeric       AS final_total
FROM
    orders
WHERE
    -- Equivalente a $match payment.status = "completed"
    data->'payment'->>'status' = 'completed'
    -- Equivalente a $match timestamp >= "2026-01-01T00:00:00Z"
    AND (data->>'timestamp')::timestamptz >= '2026-01-01 00:00:00Z'

-- Equivalente a $sort payment.final_total: -1
ORDER BY
    (data->'payment'->>'final_total')::numeric DESC

-- Equivalente a $limit 1000
LIMIT 1000;

