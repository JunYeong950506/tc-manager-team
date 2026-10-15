"""Explicit lossless v1 -> v2 migration into a new file; never edits the source."""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3

import tc_storage as storage


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def verify(source, destination, progress=None):
    results = {}
    with closing(sqlite3.connect(Path(source).resolve().as_uri() + '?mode=ro', uri=True)) as old, \
            closing(sqlite3.connect(Path(destination).resolve().as_uri() + '?mode=ro', uri=True)) as new:
        old.execute('BEGIN')
        new.row_factory = storage.StoredRow
        tables = [row[0] for row in old.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for table in tables:
            before = old.execute('SELECT * FROM ' + quote(table) + ' ORDER BY rowid')
            after = new.execute('SELECT * FROM ' + quote(table) + ' ORDER BY rowid')
            digest, count = hashlib.sha256(), 0
            for row in before:
                candidate = after.fetchone()
                if candidate is None or tuple(candidate) != row:
                    raise ValueError('논리 데이터 불일치: ' + table + ':' + str(count))
                digest.update(json.dumps(row, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
                digest.update(b'\n')
                count += 1
            if after.fetchone() is not None:
                raise ValueError('추가 행 발견: ' + table)
            results[table] = dict(rows=count, logical_sha256=digest.hexdigest())
            if progress:
                progress('verified', table, count)
        if new.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or new.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('변환 DB 무결성 검사 실패')
        for (digest,) in storage.raw_execute(new, 'SELECT hash FROM json_objects'):
            storage.unpack(new, storage.PREFIX + digest)
        objects = new.execute('SELECT COUNT(*),SUM(length(data)) FROM json_objects').fetchone()
    return dict(tables=results, objects=objects[0], compressed_bytes=objects[1], integrity='ok', exact_content_preserved=True)


def compact(source, output, progress=None):
    source, output = Path(source).resolve(), Path(output).resolve()
    if source == output or output.exists() or not source.is_file():
        raise ValueError('원본은 존재해야 하며 출력은 다른 새 파일이어야 합니다')
    output.parent.mkdir(parents=True, exist_ok=True)
    pending = output.with_name(output.name + '.pending')
    descriptor = os.open(pending, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.close(descriptor)
    original_hash = file_hash(source)
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as old, closing(sqlite3.connect(pending)) as new:
        old.execute('BEGIN')
        if old.execute('PRAGMA user_version').fetchone()[0] != 1:
            raise ValueError('DB 형식 1만 변환할 수 있습니다')
        if old.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('원본 DB 무결성 검사 실패')
        schema = old.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY rowid").fetchall()
        new.execute(storage.DDL)
        for kind, name, sql in schema:
            if kind == 'table':
                new.execute(sql)
        for kind, name, sql in schema:
            if kind != 'table':
                continue
            cursor = old.execute('SELECT rowid,* FROM ' + quote(name) + ' ORDER BY rowid')
            columns = [column[0] for column in cursor.description]
            insert = 'INSERT INTO ' + quote(name) + ' (rowid,' + ','.join(quote(c) for c in columns[1:]) + ') VALUES (' + ','.join('?' for _ in columns) + ')'
            count = 0
            new.execute('BEGIN')
            for row in cursor:
                new.execute(insert, (row[0], *storage.encode_row(new, name, columns[1:], row[1:])))
                count += 1
            new.commit()
            if progress:
                progress('copied', name, count)
        for kind, name, sql in schema:
            if kind != 'table':
                new.execute(sql)
        new.execute('PRAGMA user_version=2')
        new.commit()
        new.execute('VACUUM')
    report = verify(source, pending, progress)
    if file_hash(source) != original_hash:
        raise ValueError('변환 중 원본 파일이 변경되었습니다. 결과를 적용하지 않았습니다')
    report.update(source=str(source), output=str(output), source_sha256=original_hash, sha256=file_hash(pending),
                  before_bytes=source.stat().st_size, after_bytes=pending.stat().st_size, storage_version=2)
    os.link(pending, output)
    pending.unlink()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    def progress(stage, table, count):
        print(json.dumps(dict(stage=stage, table=table, rows=count)), flush=True)
    result = compact(args.source, args.output, progress)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'tables'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
