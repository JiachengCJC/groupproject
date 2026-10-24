"""Copy a legacy SQLite workspace to an empty target database.

Dry-run is the default. The source is read through SQLite's online backup API
into a private temporary copy; additive migrations never touch the source.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from .config import Settings
from .database import Database, identifier

TABLES=("workspaces","users","uploads","datasets","documents","chunks","runs","reviews","benchmarks","app_records","audit_events","tasks")


def migrate_sqlite(source_path,target_url,*,apply=False,analytics_url="",analytics_role=""):
    source_path=Path(source_path)
    if not source_path.is_file():raise ValueError("The source SQLite database does not exist.")
    if not str(target_url).startswith(("postgresql://","postgres://","postgresql+psycopg://")):
        if Path(target_url).resolve()==source_path.resolve():raise ValueError("Source and target must be different databases.")
    with tempfile.TemporaryDirectory(prefix="market-lens-migration-") as temp:
        copied=Path(temp)/"source.sqlite3"
        with sqlite3.connect(source_path.resolve().as_uri()+"?mode=ro",uri=True) as original,sqlite3.connect(copied) as backup:
            original.backup(backup)
        source=Database(copied);source.initialize()
        with source.connect() as con:
            counts={table:con.execute(f"SELECT COUNT(*) AS n FROM {identifier(table)}").fetchone()["n"] for table in TABLES}
            data_tables=[dict(r) for r in con.execute("SELECT table_name,columns_json FROM datasets")]
        report={"applied":False,"tables":counts,"data_tables":len(data_tables),"sessions_migrated":False,"source_modified":False}
        if not apply:return report
        target=Database(target_url,analytics_url=analytics_url,analytics_role=analytics_role)
        target.initialize()
        with source.connect() as src,target.connect() as dst:
            target._lock(dst)
            if target.dialect=="postgresql":dst.execute("SELECT pg_advisory_xact_lock(21112029)")
            for table in TABLES:
                if table=="workspaces":continue
                if dst.execute(f"SELECT COUNT(*) AS n FROM {identifier(table)}").fetchone()["n"]:
                    raise ValueError("Migration requires an empty target database. Existing records were not replaced.")
            for dataset in data_tables:
                columns=json.loads(dataset["columns_json"])
                definitions=", ".join(identifier(c["name"])+" "+("DOUBLE PRECISION" if target.dialect=="postgresql" and c["type"]=="REAL" else c["type"]) for c in columns)
                table=identifier(dataset["table_name"])
                dst.execute(f"CREATE TABLE {table} ({definitions})")
                rows=src.execute(f"SELECT * FROM {table}")
                while batch:=rows.fetchmany(1000):
                    dst.executemany(f"INSERT INTO {table} VALUES ("+",".join("?" for _ in columns)+")",[tuple(r) for r in batch])
                if target.dialect=="postgresql" and analytics_role:
                    dst.execute(f"GRANT SELECT ON {table} TO {identifier(analytics_role)}")
                source_count=src.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
                target_count=dst.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
                if source_count!=target_count:raise ValueError("Dataset row count mismatch; migration rolled back.")
            for table in TABLES:
                cursor=src.execute(f"SELECT * FROM {identifier(table)}")
                names=[item[0] for item in cursor.description]
                statement=f"INSERT INTO {identifier(table)} ("+",".join(identifier(name) for name in names)+") VALUES ("+",".join("?" for _ in names)+")"
                if table=="workspaces":statement+=" ON CONFLICT(id) DO UPDATE SET name=excluded.name"
                while batch:=cursor.fetchmany(500):
                    rows=[]
                    for original in batch:
                        row=dict(original)
                        if table=="tasks" and row["status"]=="running":
                            row.update(status="queued",worker_id=None,lease_token=None,lease_expires=None,error="Recovered after migration.")
                        rows.append(tuple(row[name] for name in names))
                    dst.executemany(statement,rows)
                actual=dst.execute(f"SELECT COUNT(*) AS n FROM {identifier(table)}").fetchone()["n"]
                if actual!=counts[table]:raise ValueError(f"{table} count mismatch; migration rolled back.")
            # Binary uploads and approved report hashes are verified before commit.
            for row in src.execute("SELECT id,sha256,content FROM uploads"):
                migrated=dst.execute("SELECT sha256,content FROM uploads WHERE id=?",(row["id"],)).fetchone()
                if bytes(row["content"])!=bytes(migrated["content"]) or row["sha256"]!=migrated["sha256"]:
                    raise ValueError("Original-file checksum verification failed; migration rolled back.")
            for row in src.execute("SELECT id,result_sha256 FROM runs"):
                migrated=dst.execute("SELECT result_sha256 FROM runs WHERE id=?",(row["id"],)).fetchone()
                if row["result_sha256"]!=migrated["result_sha256"]:
                    raise ValueError("Report verification failed; migration rolled back.")
        report["applied"]=True
        return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",required=True,type=Path)
    parser.add_argument("--apply",action="store_true",help="Copy records; otherwise only report the migration scope.")
    args=parser.parse_args()
    settings=Settings.from_env()
    target=settings.database_url or settings.db_path
    report=migrate_sqlite(args.source,target,apply=args.apply,analytics_url=settings.analytics_database_url,analytics_role=settings.analytics_role)
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=="__main__":main()
