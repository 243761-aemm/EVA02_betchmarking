// =============================================================================
// q2_sort_topk.js
// Q2 — Massive Sort & Top-K (estrés de buffer)
// IT2iD - Bases de Datos en la Nube 2026
//
// Descripción (PDF §5.2):
//   Filtrar órdenes completadas en un rango de fechas, ordenar descendente
//   por payment.final_total y obtener el Top-1000 sin índices previos en el total.
//
// Variantes de estrés de memoria (PDF):
//   - allowDiskUse: false  -> falla si no cabe en RAM (OOM / error)
//   - allowDiskUse: true   -> vuelca a disco temporal
//
// Ejecutar con la variable ALLOW_DISK_USE controlada desde benchmark_runner.
// =============================================================================

db = db.getSiblingDB('benchmark');

// ---- Variante controlada por parámetro ----
// Por defecto usa allowDiskUse: false para medir el límite de buffer.
// El benchmark_runner sobreescribe ALLOW_DISK_USE según la variante.
var allowDisk = (typeof ALLOW_DISK_USE !== 'undefined') ? ALLOW_DISK_USE : false;

var pipeline = [
  // Etapa 1: Filtrar por status y rango de fechas
  {
    "$match": {
      "payment.status": "completed",
      "timestamp": { "$gte": "2026-01-01T00:00:00Z" }
    }
  },

  // Etapa 2: Ordenar descendente por payment.final_total
  // Sin índice previo en final_total -> estrés de buffer de ordenamiento
  { "$sort": { "payment.final_total": -1 } },

  // Etapa 3: Top-1000
  { "$limit": 1000 },

  // Etapa 4: Proyección mínima
  {
    "$project": {
      "order_id":          1,
      "customer.id":       1,
      "payment.final_total": 1
    }
  }
];

try {
  var result = db.orders.aggregate(pipeline, {
    allowDiskUse: allowDisk,
    comment: "Q2_sort_topk_allowDisk_" + allowDisk
  });

  var count = 0;
  result.forEach(function(doc) { count++; });
  print("Q2 MongoDB (allowDiskUse=" + allowDisk + ") - Resultados: " + count);

} catch (e) {
  // Captura error de OOM cuando allowDiskUse: false y no cabe en RAM
  print("Q2 MongoDB ERROR (allowDiskUse=" + allowDisk + "): " + e.message);
  print("OOM_KILLED: true");
}

