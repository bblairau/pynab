import regex
import time
import io
import pyhashxx
import struct

from pynab.db import db_session, engine, Part, Segment
from pynab import log

from sqlalchemy.orm import Load, subqueryload


def generate_hash(subject, posted_by, group_name, total_segments):
    """Generates a mostly-unique temporary hash for a part."""
    hash = pyhashxx.hashxx(subject.encode('utf-8'), posted_by.encode('utf-8'),
                           group_name.encode('utf-8'), struct.pack('I', total_segments)
    )

    return hash


def save_all(parts):
    """Save a set of parts to the DB, in a batch if possible."""

    if parts:
        start = time.time()
        group_name = list(parts.values())[0]['group_name']

        with db_session() as db:
            # this is a little tricky. parts have no uniqueness at all.
            # no uniqid and the posted dates can change since it's based off the first
            # segment that we see in that part, which is different for each scan.
            # what we do is get the next-closest thing (subject+author+group) and
            # order it by oldest first, so when it's building the dict the newest parts
            # end on top (which are the most likely to be being saved to).

            # realistically, it shouldn't be a big problem - parts aren't stored in the db
            # for very long anyway, and they're only a problem while there. saving 500 million
            # segments to the db is probably not a great idea anyway.
            existing_parts = dict(
                ((part.hash, part) for part in
                    db.query(Part.id, Part.hash).filter(Part.hash.in_(parts.keys())).filter(Part.group_name==group_name).order_by(Part.posted.asc()).all()
                )
            )

            part_inserts = []
            for hash, part in parts.items():
                existing_part = existing_parts.get(hash, None)
                if not existing_part:
                    segments = part.pop('segments')
                    part_inserts.append(part)
                    part['segments'] = segments

            if part_inserts:
                ordering = ['hash', 'subject', 'group_name', 'posted', 'posted_by', 'total_segments', 'xref']

                s = io.StringIO()
                for part in part_inserts:
                    for item in ordering:
                        if item == 'posted':
                            s.write('"' + part[item].replace(tzinfo=None).strftime('%Y-%m-%d %H:%M:%S').replace('"', '\\"') + '",')
                        elif item == 'xref':
                            # leave off the tab
                            s.write('"' + part[item].replace('"', '\\"') + '"')
                        else:
                            s.write('"' + str(part[item]).replace('"', '\\"') + '",')
                    s.write("\n")
                s.seek(0)

                conn = engine.raw_connection()
                cur = conn.cursor()
                insert_start = time.time()
                cur.copy_expert("COPY parts ({}) FROM STDIN WITH CSV ESCAPE E'\\\\'".format(', '.join(ordering)), s)
                conn.commit()
                insert_end = time.time()
                log.debug('Time: {:.2f}s'.format(insert_end - insert_start))

                #engine.execute(Part.__table__.insert(), part_inserts)

            existing_parts = dict(
                ((part.hash, part) for part in
                    db.query(Part)
                    .options(
                        subqueryload('segments'),
                        Load(Part).load_only(Part.id, Part.hash),
                        Load(Segment).load_only(Segment.id, Segment.segment)
                    )
                    .filter(Part.hash.in_(parts.keys()))
                    .filter(Part.group_name==group_name)
                    .order_by(Part.posted.asc())
                    .all()
                )
            )

            segment_inserts = []
            for hash, part in parts.items():
                existing_part = existing_parts.get(hash, None)
                if existing_part:
                    segments = dict(((s.segment, s) for s in existing_part.segments))
                    for segment_number, segment in part['segments'].items():
                        if int(segment_number) not in segments:
                            segment['part_id'] = existing_part.id
                            segment_inserts.append(segment)
                        else:
                            # we hit a duplicate message for a part
                            # kinda wish people would stop reposting shit constantly
                            pass
                else:
                    log.critical('i\'ve made a huge mistake')
                    return False

            if segment_inserts:
                ordering = ['segment', 'size', 'message_id', 'part_id']

                s = io.StringIO()
                for segment in segment_inserts:
                    for item in ordering:
                        if item == 'part_id':
                            # leave off the tab
                            s.write(str(segment[item]))
                        else:
                            s.write(str(segment[item]) + "\t")
                    s.write("\n")
                s.seek(0)

                conn = engine.raw_connection()
                cur = conn.cursor()
                insert_start = time.time()
                cur.copy_from(s, 'segments', columns=ordering)
                conn.commit()
                insert_end = time.time()
                log.debug('parts: postgres copy time: {:.2f}s'.format(insert_end - insert_start))

                #engine.execute(Segment.__table__.insert(), segment_inserts)

        end = time.time()

        log.debug('parts: saved {} parts and {} segments in {:.2f}s'.format(
            len(part_inserts),
            len(segment_inserts),
            end - start
        ))

    return True


def is_blacklisted(subject, group_name, blacklists):
    for blacklist in blacklists:
        if regex.search(blacklist.group_name, group_name):
            # too spammy
            #log.debug('{0}: Checking blacklist {1}...'.format(group_name, blacklist['regex']))
            if regex.search(blacklist.regex, subject):
                return True
    return False