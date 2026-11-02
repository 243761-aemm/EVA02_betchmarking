-- =============================================================================
-- q1_unwind_filter_group.sql
-- Q1 — Unwind, Filter & Grouping (saturación de CPU)
-- IT2iD - Bases de Datos en la Nube 2026
--
-- Descripción (PDF §5.1):
--   Desanidar el arreglo de ítems (jsonb_array_elements), filtrar price > 50,
--   calcular subtotal (qty * price) y agrupar sumatoria y promedio
--   por país y categoría.
--
-- Equivalente al pipeline MongoDB Q1.
-- =============================================================================

-- Habilitar temporización y buffers para EXPLAIN ANALYZE
-- (descomentar para plan de ejecución)
-- EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)

SELECT
    data->'customer'->'geo'->>'country'           AS country,
    item->>'category'                              AS category,
    SUM((item->>'qty')::numeric
      * (item->>'price')::numeric)                 AS total_revenue,
    AVG((item->>'qty')::numeric
      * (item->>'price')::numeric)                 AS avg_revenue,
    COUNT(*)                                       AS count
FROM
    orders,
    -- Equivalente a $unwind: expande cada elemento del arreglo items
    jsonb_array_elements(data->'items') AS item
WHERE
    -- Equivalente a $match: price > 50
    (item->>'price')::numeric > 50
GROUP BY
    1, 2   -- country, category
ORDER BY
    total_revenue DESC;

