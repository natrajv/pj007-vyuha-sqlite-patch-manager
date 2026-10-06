import sqlite3
import shutil
from .comparator import connect, tables, columns, pk_columns, quote_ident

def merge_databases(source_path, target_path, output_path, change_structure=False, delete_rows=False, update_rows=False):
    shutil.copy2(target_path, output_path)
    with sqlite3.connect(output_path) as out, connect(source_path) as s, connect(target_path) as t:
        source_tables, target_tables = set(tables(s)), set(tables(t))
        if change_structure:
            for table in sorted(source_tables - target_tables):
                row = s.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
                if not row or not row[0]: continue
                out.execute(row[0])
                cols = [x[1] for x in columns(s, table)]
                rows = s.execute(f"SELECT * FROM {quote_ident(table)}").fetchall()
                if rows:
                    out.executemany(f"INSERT INTO {quote_ident(table)} VALUES ({','.join(['?']*len(cols))})", rows)
        for table in sorted(source_tables & target_tables):
            sc, tc = columns(s, table), columns(t, table)
            scol, tcol = [x[1] for x in sc], [x[1] for x in tc]
            if change_structure:
                for col in [x for x in sc if x[1] not in tcol]:
                    out.execute(f"ALTER TABLE {quote_ident(table)} ADD COLUMN {quote_ident(col[1])} {col[2] or ''}")
            common = [c for c in scol if c in tcol]
            spk, tpk = pk_columns(s, table), pk_columns(t, table)
            key_cols = spk if spk and spk == tpk else ([c for c in common] if not spk and not tpk else [])
            if not key_cols: continue
            src = s.execute(f"SELECT {','.join(quote_ident(c) for c in common)} FROM {quote_ident(table)}").fetchall()
            tgt = t.execute(f"SELECT {','.join(quote_ident(c) for c in common)} FROM {quote_ident(table)}").fetchall()
            idx={c:i for i,c in enumerate(common)}; pk_idx=[idx[c] for c in key_cols]
            target_keys={tuple(r[i] for i in pk_idx) for r in tgt}; source_keys={tuple(r[i] for i in pk_idx) for r in src}
            if update_rows:
                set_cols=[c for c in common if c not in key_cols]
                if set_cols:
                    ass=','.join(f"{quote_ident(c)}=?" for c in set_cols); where=' AND '.join(f"{quote_ident(c)}=?" for c in key_cols)
                    for row in src:
                        key=tuple(row[i] for i in pk_idx)
                        if key in target_keys:
                            out.execute(f"UPDATE {quote_ident(table)} SET {ass} WHERE {where}",[row[idx[c]] for c in set_cols]+[row[idx[c]] for c in key_cols])
            for row in src:
                key=tuple(row[i] for i in pk_idx)
                if key not in target_keys:
                    out.execute(f"INSERT INTO {quote_ident(table)} ({','.join(quote_ident(c) for c in common)}) VALUES ({','.join(['?']*len(common))})",[row[idx[c]] for c in common])
            if delete_rows:
                where=' AND '.join(f"{quote_ident(c)}=?" for c in key_cols)
                for key in target_keys-source_keys: out.execute(f"DELETE FROM {quote_ident(table)} WHERE {where}",key)
        out.commit()
