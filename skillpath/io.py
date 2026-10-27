"""Export / import: CSV (all scales), JSON Lines and SQL seed (small scales), manifest, docs.

Files are written in a fixed order with rows sorted by primary key, so the same
seed always produces byte-identical files (checked by `reproduce`).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from . import config as C
from .schema import RELATIONSHIPS, TABLE_ORDER, TABLES

DT_FMT = "%Y-%m-%dT%H:%M:%S"
SQL_TYPES = {"string": "TEXT", "integer": "INTEGER", "float": "REAL", "boolean": "INTEGER", "datetime": "TEXT"}


# ------------------------------------------------------------------ write
def _prepare(name: str, df: pd.DataFrame) -> pd.DataFrame:
    t = TABLES[name]
    df = df[[f.name for f in t.fields]].copy()
    for f in t.fields:
        if f.type == "integer":
            df[f.name] = df[f.name].astype("Int64")
        elif f.type == "datetime":
            df[f.name] = pd.to_datetime(df[f.name])
    return df.sort_values(list(t.primary_key), kind="mergesort").reset_index(drop=True)


def write_tables(tables: dict, folder: Path, formats=("csv",)) -> dict:
    """Write every table; returns {relative file: sha256}."""
    folder = Path(folder)
    (folder / "tables").mkdir(parents=True, exist_ok=True)
    digests = {}
    for name in TABLE_ORDER:
        df = _prepare(name, tables[name])
        path = folder / "tables" / f"{name}.csv"
        df.to_csv(path, index=False, date_format=DT_FMT, lineterminator="\n")
        digests[f"tables/{name}.csv"] = sha256(path)
        if "json" in formats:
            (folder / "json").mkdir(exist_ok=True)
            jpath = folder / "json" / f"{name}.jsonl"
            out = df.copy()
            for f in TABLES[name].fields:
                if f.type == "datetime":
                    out[f.name] = out[f.name].dt.strftime(DT_FMT)
            out.to_json(jpath, orient="records", lines=True, force_ascii=False)
    if "sql" in formats:
        write_sql_seed(tables, folder / "seed.sql")
    (folder / "schema.sql").write_text(ddl(), encoding="utf-8")
    (folder / "schema.json").write_text(json.dumps(schema_json(), indent=2), encoding="utf-8")
    return digests


def write_frame(df: pd.DataFrame, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, date_format=DT_FMT, lineterminator="\n")
    return sha256(path)


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ------------------------------------------------------------------ read
def read_tables(folder: Path) -> dict:
    folder = Path(folder) / "tables"
    out = {}
    for name in TABLE_ORDER:
        t = TABLES[name]
        dtypes, dates = {}, []
        for f in t.fields:
            if f.type == "string":
                dtypes[f.name] = "object"
            elif f.type == "integer":
                dtypes[f.name] = "Int64"
            elif f.type == "float":
                dtypes[f.name] = "float64"
            elif f.type == "datetime":
                dtypes[f.name] = "object"
                dates.append(f.name)
        df = pd.read_csv(folder / f"{name}.csv", dtype=dtypes, keep_default_na=True)
        for f in t.fields:
            if f.type == "boolean":
                df[f.name] = df[f.name].map({True: True, False: False, "True": True, "False": False})
        for col in dates:
            df[col] = pd.to_datetime(df[col], format=DT_FMT)
        df = df.astype({c: object for c, v in dtypes.items() if v == "object" and c not in dates})
        out[name] = df
    return out


# ------------------------------------------------------------------ SQL
def ddl() -> str:
    lines = ["-- Generated from skillpath/schema.py. Load with: python -m skillpath load-sqlite",
             "PRAGMA foreign_keys = ON;", ""]
    for name in TABLE_ORDER:
        t = TABLES[name]
        cols = []
        for f in t.fields:
            col = f"  {f.name} {SQL_TYPES[f.type]}"
            if f.required:
                col += " NOT NULL"
            if f.allowed:
                vals = ", ".join(f"'{v}'" for v in f.allowed)
                col += f" CHECK ({f.name} IN ({vals}))"
            if f.min is not None and f.type in ("integer", "float"):
                col += f" CHECK ({f.name} >= {f.min})"
            if f.max is not None and f.type in ("integer", "float"):
                col += f" CHECK ({f.name} <= {f.max})"
            cols.append(col)
        cols.append(f"  PRIMARY KEY ({', '.join(t.primary_key)})")
        for f in t.fields:
            if f.fk:
                ptable, pcol = f.fk.split(".")
                cols.append(f"  FOREIGN KEY ({f.name}) REFERENCES {ptable}({pcol})")
        lines.append(f"CREATE TABLE {name} (\n" + ",\n".join(cols) + "\n);\n")
    return "\n".join(lines)


def _sql_value(v, ftype):
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return "NULL"
    if ftype == "datetime":
        return f"'{pd.Timestamp(v).strftime(DT_FMT)}'"
    if ftype == "boolean":
        return "1" if bool(v) else "0"
    if ftype in ("integer", "float"):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def write_sql_seed(tables: dict, path: Path) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(ddl())
        fh.write("\nBEGIN TRANSACTION;\n")
        for name in TABLE_ORDER:
            t = TABLES[name]
            df = _prepare(name, tables[name])
            cols = ", ".join(f.name for f in t.fields)
            types = [f.type for f in t.fields]
            for row in df.itertuples(index=False):
                vals = ", ".join(_sql_value(v, ty) for v, ty in zip(row, types))
                fh.write(f"INSERT INTO {name} ({cols}) VALUES ({vals});\n")
        fh.write("COMMIT;\n")


def load_sqlite(data_dir: Path, db_path: Path) -> dict:
    """Load the CSVs into SQLite with foreign keys ON. Rows that break FKs/CHECKs are reported."""
    import sqlite3
    tables = read_tables(data_dir)
    db_path = Path(db_path)
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    con.executescript(ddl())
    report = {}
    for name in TABLE_ORDER:
        t = TABLES[name]
        df = _prepare(name, tables[name])
        cols = [f.name for f in t.fields]
        sql = f"INSERT INTO {name} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
        ok = rejected = 0
        for row in df.itertuples(index=False):
            vals = []
            for v, f in zip(row, t.fields):
                if v is None or (not isinstance(v, str) and pd.isna(v)):
                    vals.append(None)
                elif f.type == "datetime":
                    vals.append(pd.Timestamp(v).strftime(DT_FMT))
                elif f.type == "boolean":
                    vals.append(int(bool(v)))
                elif f.type == "integer":
                    vals.append(int(v))
                elif f.type == "float":
                    vals.append(float(v))
                else:
                    vals.append(v)
            try:
                con.execute(sql, vals)
                ok += 1
            except sqlite3.IntegrityError:
                rejected += 1
        report[name] = {"loaded": ok, "rejected_by_constraints": rejected}
    con.commit()
    con.close()
    return report


# ------------------------------------------------------------------ docs
def schema_json() -> dict:
    return {
        name: {
            "entity": t.entity, "description": t.description, "primary_key": list(t.primary_key),
            "rules": list(t.rules),
            "fields": [{k: v for k, v in f.__dict__.items() if v not in ("", (), None)} for f in t.fields],
        }
        for name, t in TABLES.items()
    }


def data_dictionary_md() -> str:
    out = ["# Data dictionary", "",
           "_Generated from `skillpath/schema.py` by `python -m skillpath docs` — do not edit by hand._", "",
           "## Relationships", "", "| Parent | Child | Cardinality | Notes |", "|---|---|---|---|"]
    out += [f"| {p} | {c} | {card} | {note} |" for p, c, card, note in RELATIONSHIPS]
    for name, t in TABLES.items():
        out += ["", f"## `{name}` ({t.entity})", "", t.description, "",
                f"Primary key: `{', '.join(t.primary_key)}`", ""]
        out += ["| Field | Type | Req. | Allowed / range | Unit | Unique | FK | Validation rule | Description |",
                "|---|---|---|---|---|---|---|---|---|"]
        for f in t.fields:
            rng = ", ".join(f.allowed) if f.allowed else ""
            if f.min is not None or f.max is not None:
                rng = f"{'' if f.min is None else f.min} .. {'' if f.max is None else f.max}"
            rule = "; ".join(x for x in (f"pattern `{f.pattern}`" if f.pattern else "", f.rule) if x)
            out.append(f"| `{f.name}` | {f.type} | {'yes' if f.required else 'no'} | {rng} | {f.unit} | "
                       f"{'yes' if f.unique else ''} | {f.fk} | {rule} | {f.description} |")
        if t.rules:
            out += ["", "Table rules:"] + [f"- {r}" for r in t.rules]
    return "\n".join(out) + "\n"


def manifest(scale: C.Scale, seed: int, digests: dict, counts: dict, command: str) -> dict:
    return {
        "dataset": "skillpath synthetic learning-activity dataset",
        "generator_version": C.GENERATOR_VERSION,
        "engine_version": C.ENGINE_VERSION,
        "mastery_method": C.MASTERY_METHOD,
        "seed": seed,
        "scale": scale.as_dict(),
        "time_window": [C.WINDOW_START.isoformat(), C.WINDOW_END.isoformat()],
        "temporal_split_cutoff": C.TEST_CUTOFF.isoformat(),
        "generator_command": command,
        "provenance": {
            "external_data_used": False,
            "statement": "All records are synthetic, produced by this repository's code from a fixed seed. "
                         "No personal, confidential or third-party data was collected or used.",
        },
        "row_counts": counts,
        "sha256": digests,
    }
