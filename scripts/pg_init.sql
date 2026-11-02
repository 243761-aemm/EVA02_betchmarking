-- =============================================================================
-- pg_init.sql - Inicialización de PostgreSQL para el benchmark
-- IT2iD - Bases de Datos en la Nube 2026
-- =============================================================================

-- Tabla orders: id UUID PRIMARY KEY, data JSONB (como especifica el PDF)
CREATE TABLE IF NOT EXISTS orders (
    id UUID PRIMARY KEY,
    data JSONB NOT NULL
);

-- PK en id (ya existe como PRIMARY KEY)
-- B-Tree sobre timestamp extraído del JSONB (texto, compatible con cast en runtime)
CREATE INDEX IF NOT EXISTS idx_timestamp_extracted
    ON orders ((data->>'timestamp'));

-- Índice GIN sobre el JSONB completo para búsquedas de contenido
CREATE INDEX IF NOT EXISTS idx_data_gin
    ON orders USING GIN (data jsonb_path_ops);

-- Índice B-Tree sobre payment.status para Q2
CREATE INDEX IF NOT EXISTS idx_payment_status
    ON orders (((data->'payment'->>'status')));

-- Configurar work_mem base (se sobreescribe en el benchmark runner por variante)
ALTER DATABASE benchmark SET work_mem = '64MB';

\echo 'PostgreSQL: Tabla orders e índices creados correctamente.'

