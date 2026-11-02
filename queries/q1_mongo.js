// =============================================================================
// q1_unwind_filter_group.js
// Q1 — Unwind, Filter & Grouping (saturación de CPU)
// IT2iD - Bases de Datos en la Nube 2026
//
// Descripción (PDF §5.1):
//   Desanidar el arreglo de ítems, filtrar price > 50,
//   calcular subtotal (qty * price) y agrupar sumatoria
//   y promedio por país y categoría.
// =============================================================================

db = db.getSiblingDB('benchmark');

var result = db.orders.aggregate(
  [
    // Etapa 1: Desanidar el arreglo items[] (un doc por ítem)
    { "$unwind": "$items" },

    // Etapa 2: Filtrar solo ítems con price > 50
    { "$match": { "items.price": { "$gt": 50 } } },

    // Etapa 3: Agrupar por país y categoría
    {
      "$group": {
        "_id": {
          "country":  "$customer.geo.country",
          "category": "$items.category"
        },
        "total_revenue": {
          "$sum": { "$multiply": ["$items.qty", "$items.price"] }
        },
        "avg_revenue": {
          "$avg": { "$multiply": ["$items.qty", "$items.price"] }
        },
        "count": { "$sum": 1 }
      }
    }
  ],
  {
    // allowDiskUse: true permite volcado a disco si se supera el límite de 100 MB en memoria
    allowDiskUse: true,
    comment: "Q1_unwind_filter_group"
  }
);

// Forzar evaluación del cursor para medir tiempo real
var count = 0;
result.forEach(function(doc) { count++; });
print("Q1 MongoDB - Grupos resultantes: " + count);

