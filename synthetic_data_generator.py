#!/usr/bin/env python3
"""
synthetic_data_generator.py
============================
Generador de dataset sintético para el benchmark de Bases de Datos en la Nube 2026.
IT2iD - Universidad Politécnica de Chiapas

Especificación del PDF:
  - 500 000 registros en formato JSON
  - 3 niveles de anidamiento promedio
  - 3 ítems por registro (promedio)
  - Atributos: order_id, timestamp, customer, items[], payment, metadata

Salida:
  - data/orders.jsonl       -> para importar en MongoDB (mongoimport)
  - data/orders_pg.csv      -> para importar en PostgreSQL (COPY)

Uso:
  python synthetic_data_generator.py [--records 500000] [--out-dir ./data]
"""

import argparse
import csv
import json
import os
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Seed para reproducibilidad
# ---------------------------------------------------------------------------
SEED = 42
random.seed(SEED)

# ---------------------------------------------------------------------------
# Datos de referencia (catálogos)
# ---------------------------------------------------------------------------
COUNTRIES = ["MX", "US", "BR", "CO", "AR", "CL", "PE", "EC", "VE", "GT"]
STATES = {
    "MX": ["Chiapas", "Jalisco", "CDMX", "Nuevo Leon", "Puebla", "Oaxaca"],
    "US": ["California", "Texas", "Florida", "New York", "Illinois"],
    "BR": ["Sao Paulo", "Rio de Janeiro", "Minas Gerais", "Bahia"],
    "CO": ["Bogota", "Antioquia", "Valle del Cauca"],
    "AR": ["Buenos Aires", "Cordoba", "Santa Fe"],
    "CL": ["Santiago", "Valparaiso", "Biobio"],
    "PE": ["Lima", "Arequipa", "Cusco"],
    "EC": ["Pichincha", "Guayas", "Azuay"],
    "VE": ["Distrito Capital", "Miranda", "Carabobo"],
    "GT": ["Guatemala", "Quetzaltenango", "Escuintla"],
}
CITIES = ["Tuxtla", "Springfield", "Metropolis", "Capital City", "Shelbyville",
          "Oakland", "Riverside", "Lakewood", "Greenville", "Franklin"]
CUSTOMER_TIERS = ["bronze", "silver", "gold", "platinum"]
CUSTOMER_SEGMENTS = ["B2C", "B2B", "enterprise", "wholesale"]
CATEGORIES = ["electronics", "clothing", "food", "furniture", "sports",
              "books", "toys", "health", "automotive", "garden"]
BRANDS = ["BrandA", "BrandB", "BrandC", "BrandD", "BrandE",
          "BrandF", "BrandG", "BrandH", "BrandI", "BrandJ"]
PAYMENT_METHODS = ["credit_card", "debit_card", "paypal", "transfer",
                   "cash", "crypto", "bnpl"]
PAYMENT_STATUSES = ["completed", "pending", "failed", "refunded", "disputed"]
GATEWAYS = ["stripe", "paypal_gw", "conekta", "mercadopago", "openpay"]
CHANNELS = ["web", "mobile_ios", "mobile_android", "api", "pos"]
FLAGS = ["new_customer", "loyalty_points", "fraud_flag", "vip", "test_order"]

# ---------------------------------------------------------------------------
# Generadores de sub-documentos
# ---------------------------------------------------------------------------

def random_date(start_year: int = 2025, end_year: int = 2026) -> str:
    """Genera un timestamp ISO-8601 aleatorio dentro del rango."""
    start = datetime(start_year, 1, 1, tzinfo=timezone.utc)
    end   = datetime(end_year, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    delta = end - start
    random_seconds = random.randint(0, int(delta.total_seconds()))
    dt = start + timedelta(seconds=random_seconds)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def gen_customer() -> dict:
    """Nivel 2: objeto customer con geo anidado (nivel 3)."""
    country = random.choice(COUNTRIES)
    state   = random.choice(STATES.get(country, ["Unknown"]))
    return {
        "id":      str(uuid.uuid4()),
        "tier":    random.choice(CUSTOMER_TIERS),
        "segment": random.choice(CUSTOMER_SEGMENTS),
        "geo": {                              # Nivel 3
            "country":     country,
            "state":       state,
            "city":        random.choice(CITIES),
            "zip":         f"{random.randint(10000, 99999)}",
            "coordinates": {
                "lat": round(random.uniform(-55.0, 55.0), 6),
                "lon": round(random.uniform(-180.0, 180.0), 6),
            },
        },
    }


def gen_item() -> dict:
    """Objeto de ítem dentro del arreglo items[]."""
    qty   = random.randint(1, 20)
    price = round(random.uniform(1.0, 500.0), 2)
    tax   = round(random.uniform(0.0, 0.21), 4)
    return {
        "sku":       f"SKU-{random.randint(100000, 999999)}",
        "category":  random.choice(CATEGORIES),
        "brand":     random.choice(BRANDS),
        "qty":       qty,
        "price":     price,
        "tax_pct":   tax,
        "discounts": {
            "pct":    round(random.uniform(0.0, 0.30), 4),
            "code":   f"DISC{random.randint(1000, 9999)}" if random.random() > 0.6 else None,
            "amount": round(price * random.uniform(0.0, 0.30), 2),
        },
    }


def gen_items(n: int) -> list:
    """Genera n ítems para el pedido."""
    return [gen_item() for _ in range(n)]


def gen_payment(items: list) -> dict:
    """Nivel 2: objeto payment con totals anidado (nivel 3)."""
    subtotal    = sum(i["qty"] * i["price"] for i in items)
    tax_amount  = sum(i["qty"] * i["price"] * i["tax_pct"] for i in items)
    discount    = sum(i["discounts"]["amount"] for i in items)
    final_total = round(subtotal + tax_amount - discount, 2)
    return {
        "method":             random.choice(PAYMENT_METHODS),
        "status":             random.choice(PAYMENT_STATUSES),
        "transaction_id":     str(uuid.uuid4()).replace("-", "").upper()[:20],
        "gateway_latency_ms": round(random.uniform(20.0, 800.0), 2),
        "totals": {           # Nivel 3
            "subtotal":    round(subtotal, 2),
            "tax_amount":  round(tax_amount, 2),
            "discount":    round(discount, 2),
            "final_total": final_total,
        },
        # Alias plano para facilitar el sort de Q2
        "final_total": final_total,
    }


def gen_metadata() -> dict:
    """Nivel 2: objeto metadata."""
    active_flags = random.sample(FLAGS, k=random.randint(0, 3))
    return {
        "channel":    random.choice(CHANNELS),
        "user_agent": f"Agent/{random.randint(1,10)}.{random.randint(0,9)}",
        "session_id": str(uuid.uuid4()),
        "flags":      active_flags,
    }


def gen_order() -> dict:
    """Genera un documento order completo con 3 niveles de anidamiento."""
    n_items  = max(1, int(random.gauss(3, 1)))   # Media=3, como especifica el PDF
    items    = gen_items(n_items)
    customer = gen_customer()
    payment  = gen_payment(items)
    metadata = gen_metadata()
    return {
        "order_id":  str(uuid.uuid4()),
        "timestamp": random_date(),
        "customer":  customer,
        "items":     items,
        "payment":   payment,
        "metadata":  metadata,
    }

# ---------------------------------------------------------------------------
# Escritura de archivos de salida
# ---------------------------------------------------------------------------

def write_jsonl(records: list[dict], path: Path) -> None:
    """Escribe los registros en formato JSONL (uno por línea) para mongoimport."""
    with open(path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def write_pg_csv(records: list[dict], path: Path) -> None:
    """
    Escribe los registros en CSV con dos columnas: id (UUID) y data (JSONB).
    Compatible con PostgreSQL COPY FROM CSV.
    """
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL)
        writer.writerow(["id", "data"])
        for rec in records:
            row_id   = rec["order_id"]          # UUID como PK
            row_data = json.dumps(rec, ensure_ascii=False)
            writer.writerow([row_id, row_data])

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generador de dataset sintético - Benchmark BD Nube 2026"
    )
    parser.add_argument(
        "--records", type=int, default=500_000,
        help="Número de registros a generar (default: 500 000)"
    )
    parser.add_argument(
        "--out-dir", type=str, default="./data",
        help="Directorio de salida (default: ./data)"
    )
    parser.add_argument(
        "--batch-size", type=int, default=10_000,
        help="Tamaño de lote para escritura progresiva (default: 10 000)"
    )
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    jsonl_path = out_dir / "orders.jsonl"
    csv_path   = out_dir / "orders_pg.csv"

    total   = args.records
    batch   = args.batch_size
    batches = (total + batch - 1) // batch

    print(f"[INFO] Generando {total:,} registros en lotes de {batch:,}...")
    print(f"[INFO] Salida JSONL: {jsonl_path}")
    print(f"[INFO] Salida CSV : {csv_path}")

    # Abrir ambos archivos y escribir en lotes (eficiente en memoria)
    with (
        open(jsonl_path, "w", encoding="utf-8") as fjsonl,
        open(csv_path,   "w", encoding="utf-8", newline="") as fcsv,
    ):
        csv_writer = csv.writer(fcsv, quoting=csv.QUOTE_ALL)
        csv_writer.writerow(["id", "data"])

        generated = 0
        for b in range(batches):
            n = min(batch, total - generated)
            for _ in range(n):
                rec = gen_order()
                # JSONL para MongoDB
                fjsonl.write(json.dumps(rec, ensure_ascii=False) + "\n")
                # CSV para PostgreSQL
                csv_writer.writerow([rec["order_id"], json.dumps(rec, ensure_ascii=False)])
                generated += 1

            pct = (generated / total) * 100
            bar = "#" * int(pct // 2) + "-" * (50 - int(pct // 2))
            print(f"\r[{bar}] {pct:5.1f}%  {generated:>10,} / {total:,}", end="", flush=True)

    print(f"\n[OK] Dataset generado exitosamente.")
    print(f"     JSONL : {jsonl_path}  ({jsonl_path.stat().st_size / 1e6:.1f} MB)")
    print(f"     CSV   : {csv_path}  ({csv_path.stat().st_size / 1e6:.1f} MB)")
    print()
    print("Próximos pasos:")
    print("  MongoDB  : mongoimport --db benchmark --collection orders --file data/orders.jsonl")
    print("  PostgreSQL: psql -U bench_user -d benchmark -c \"\\COPY orders(id,data) FROM 'data/orders_pg.csv' CSV HEADER\"")


if __name__ == "__main__":
    main()

