import psycopg, pymongo, json

def explain_all():
    print("=== EXPLAIN MONGO Q2 ===")
    try:
        c = pymongo.MongoClient('mongodb://localhost:27017/')
        db = c.benchmark
        res = db.command('explain', {
            'aggregate': 'orders',
            'pipeline': [
                {'$match': {'payment.status': 'completed', 'timestamp': {'$gte': '2026-01-01T00:00:00Z'}}},
                {'$sort': {'payment.final_total': -1}},
                {'$limit': 1000},
                {'$project': {'order_id': 1, 'customer.id': 1, 'payment.final_total': 1}}
            ],
            'cursor': {}
        }, verbosity='executionStats')
        stats = res.get('executionStats', {})
        print("executionTimeMillis:", stats.get('executionTimeMillis'))
        print("totalDocsExamined:", stats.get('totalDocsExamined'))
        print("nReturned:", stats.get('nReturned'))
    except Exception as e:
        print("Mongo error:", e)

    print("\n=== EXPLAIN POSTGRES Q2 (work_mem=4MB vs 64MB) ===")
    try:
        conn = psycopg.connect('host=localhost port=5433 dbname=benchmark user=bench_user password=bench_pass')
        cur = conn.cursor()
        
        # 4MB
        cur.execute("SET work_mem = '4MB';")
        cur.execute("""EXPLAIN (ANALYZE, BUFFERS)
SELECT data->>'order_id' AS order_id,
       data->'customer'->>'id' AS customer_id,
       (data->'payment'->>'final_total')::numeric AS final_total
FROM orders
WHERE data->'payment'->>'status' = 'completed'
  AND data->>'timestamp' >= '2026-01-01T00:00:00Z'
ORDER BY (data->'payment'->>'final_total')::numeric DESC
LIMIT 1000;""")
        for r in cur.fetchall():
            print("  [4MB]  ", r[0])

        # 64MB
        cur.execute("SET work_mem = '64MB';")
        cur.execute("""EXPLAIN (ANALYZE, BUFFERS)
SELECT data->>'order_id' AS order_id,
       data->'customer'->>'id' AS customer_id,
       (data->'payment'->>'final_total')::numeric AS final_total
FROM orders
WHERE data->'payment'->>'status' = 'completed'
  AND data->>'timestamp' >= '2026-01-01T00:00:00Z'
ORDER BY (data->'payment'->>'final_total')::numeric DESC
LIMIT 1000;""")
        for r in cur.fetchall():
            print("  [64MB] ", r[0])

        conn.close()
    except Exception as e:
        print("PG error:", e)

if __name__ == '__main__':
    explain_all()

