#!/usr/bin/env python3
"""Estimate how much space media dedup would save, straight from the Signal database.

Usage: measure_duplicates.py SIGNAL_SRC
  SIGNAL_SRC  a Signal Desktop folder or a dir prepared by pipeline/prepare_source.sh

Needs the signal-export package (sigexport) importable, e.g. run it with the
pipx venv python. Prints counts and sizes only: no names, no messages, no paths.
"""
import sys
from pathlib import Path

try:
    from sigexport import data
except ImportError:
    sys.exit("sigexport not importable; run with the python from its pipx venv")

if len(sys.argv) != 2:
    sys.exit(__doc__)

c = data.get_signal_database(Path(sys.argv[1]).expanduser(), None, None)


def one(sql):
    c.execute(sql)
    return c.fetchone()


GB = 1e9
rows, size = one("select count(*), coalesce(sum(size),0) from message_attachments where path is not null")
uniq, usize = one("""select count(*), coalesce(sum(s),0) from (select coalesce(plaintextHash, path) h, max(size) s
                     from message_attachments where path is not null group by h)""")
groups, gwaste = one("""select count(*), coalesce(sum((nr-1)*s),0) from (select plaintextHash, count(*) nr,
                        count(distinct conversationId) nc, max(size) s from message_attachments
                        where path is not null and plaintextHash is not null group by plaintextHash
                        having nr > 1 and nc > 1)""")
print(f"attachment references      : {rows:,}")
print(f"export size without dedup  : {size/GB:.1f} GB")
print(f"unique content             : {uniq:,} files, {usize/GB:.1f} GB")
print(f"saved by dedup             : {(size-usize)/GB:.1f} GB")
print(f"duplicate groups across chats: {groups:,} ({gwaste/GB:.1f} GB)")
