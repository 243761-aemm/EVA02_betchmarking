// =============================================================================
// mongo_init.js - Inicialización de MongoDB para el benchmark
// IT2iD - Bases de Datos en la Nube 2026
// =============================================================================

db = db.getSiblingDB('benchmark');

// Crear colección orders sin sharding (WiredTiger)
db.createCollection('orders');

// Índice simple en timestamp (como especifica el PDF)
db.orders.createIndex({ "timestamp": 1 }, { name: "idx_timestamp" });

// Índice simple en order_id (como especifica el PDF)
db.orders.createIndex({ "order_id": 1 }, { name: "idx_order_id", unique: true });

print("MongoDB: Colección 'orders' e índices creados correctamente.");

