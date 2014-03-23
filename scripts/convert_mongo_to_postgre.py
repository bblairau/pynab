import os
import sys
import pymongo
import gridfs
import sqlalchemy.orm
import datetime

sys.path.append(os.path.join(os.path.dirname(os.path.realpath(__file__)), '..'))

import config
from pynab.db import db, Base
import pynab.db


def mongo_connect():
    return pymongo.MongoClient(config.mongo.get('host'), config.mongo.get('port'))[config.mongo.get('db')]


if __name__ == '__main__':
    mongo = mongo_connect()
    fs = gridfs.GridFS(mongo)
    postgre = sqlalchemy.orm.sessionmaker(bind=db)()

    recreate = True

    if recreate:
        Base.metadata.drop_all(db)
        Base.metadata.create_all(db)

        print('Copying blacklists...')
        for blacklist in mongo.blacklists.find():
            blacklist.pop('_id')
            blacklist['status'] = bool(blacklist['status'])

            b = pynab.db.Blacklist(**blacklist)
            postgre.add(b)

        postgre.commit()

        print('Copying regexes...')
        for regex in mongo.regexes.find():
            regex['id'] = regex['_id']
            regex.pop('_id')
            regex.pop('category_id')
            regex['status'] = bool(regex['status'])

            r = pynab.db.Regex(**regex)
            postgre.add(r)

        postgre.commit()

        print('Copying users...')
        for user in mongo.users.find():
            user.pop('_id')

            u = pynab.db.User(**user)
            postgre.add(u)

        postgre.commit()

        print('Copying TV shows...')
        for tvshow in mongo.tvrage.find():
            tvshow['id'] = tvshow['_id']
            tvshow.pop('_id')

            tv = pynab.db.TvShow(**tvshow)
            postgre.add(tv)

        postgre.commit()

        print('Copying movies...')
        for movie in mongo.imdb.find():
            movie['id'] = movie['_id']
            movie.pop('_id')
            if 'genre' in movie:
                movie['genre'] = ','.join(movie['genre'])

            m = pynab.db.Movie(**movie)
            postgre.add(m)

        postgre.commit()

        print('Copying groups...')
        for group in mongo.groups.find():
            group.pop('_id')
            group['active'] = bool(group['active'])

            g = pynab.db.Group(**group)
            postgre.add(g)

        postgre.commit()

        print('Copying categories...')
        for category in mongo.categories.find():
            category['id'] = category['_id']
            category.pop('_id')
            category.pop('min_size')
            category.pop('max_size')

            c = pynab.db.Category(**category)
            postgre.add(c)

        postgre.commit()

        print('Copying Releases, NZBs, NFOs and file data...')
        max_age = datetime.datetime.now() - datetime.timedelta(days=2045)
        active_groups = [g['_id'] for g in mongo.groups.find({'active': 1})]

        print(mongo.releases.find({'posted': {'$gte': max_age}, 'group._id': {'$in': active_groups}}).count())

        for release in mongo.releases.find({'posted': {'$gte': max_age}, 'group._id': {'$in': active_groups}}):
            print('Processing {}...'.format(release['search_name']))

            release.pop('_id')
            release.pop('id')
            c = postgre.query(pynab.db.Category).filter(pynab.db.Category.id==release['category']['_id']).first()
            release['category'] = c
            release.pop('completion')
            release.pop('file_count')

            # can't get file data because it's not informative enough
            if 'files' in release:
                release.pop('files')

            g = postgre.query(pynab.db.Group).filter(pynab.db.Group.name == release['group']['name']).first()
            release['group'] = g

            if 'regex' in release:
                if release['regex']:
                    r = postgre.query(pynab.db.Regex).filter(pynab.db.Regex.regex == release['regex']['regex']).first()
                    release['regex'] = r
                else:
                    release.pop('regex')


            if 'imdb' in release and release['imdb'] and '_id' in release['imdb']:
                release['movie'] = postgre.query(pynab.db.Movie).filter(pynab.db.Movie.id==release['imdb']['_id']).first()
            if 'imdb' in release:
                release.pop('imdb')

            if release['nfo']:
                data = fs.get(release['nfo']).read()
                n = pynab.db.NFO(data=data)
                release['nfo'] = n
            else:
                release.pop('nfo')

            if release['nzb']:
                data = fs.get(release['nzb']).read()
                n = pynab.db.NZB(data=data)
                release['nzb'] = n
            else:
                release.pop('nzb')

            release.pop('size')
            release.pop('spotnab_id')
            release.pop('total_parts')

            if release['tv']:
                e = postgre.query(pynab.db.Episode)\
                    .filter(pynab.db.Episode.clean_name==release['tv']['clean_name'])\
                    .filter(pynab.db.Episode.series_full==release['tv']['series_full']).first()
                if not e:
                    e = pynab.db.Episode(**release['tv'])

                release['episode'] = e

            if 'tv' in release:
                release.pop('tv')

            if 'tvdb' in release:
                release.pop('tvdb')

            if 'tvrage' in release:
                if release['tvrage']:
                    if '_id' in release['tvrage']:
                        t = postgre.query(pynab.db.TvShow).filter(pynab.db.TvShow.id==release['tvrage']['_id']).first()
                        release['tvshow'] = t
                release.pop('tvrage')

            if 'updated' in release:
                release.pop('updated')
            release.pop('nzb_size')

            if 'passworded' in release:
                if release['passworded'] is False:
                    release['passworded'] = 0
                if release['passworded'] is True:
                    release['passworded'] = 1
                if release['passworded'] == 'potentially':
                    release['passworded'] = 2
                if release['passworded'] == 'unknown':
                    release['passworded'] = 3

            if 'unwanted' in release:
                release.pop('unwanted')

            if 'req_id' in release:
                release.pop('req_id')

            import pprint
            #pprint.pprint(release)
            r = pynab.db.Release(**release)
            postgre.add(r)
            postgre.flush()

        postgre.commit()



