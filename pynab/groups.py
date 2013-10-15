from pynab import log
from pynab.db import db
from pynab.server import Server
from pynab import parts
import config

MESSAGE_LIMIT = config.site['message_scan_limit']


def backfill(group_name, date=None):
    log.info('{}: Backfilling group...'.format(group_name))

    with Server() as server:
        _, count, first, last, _ = server.group(group_name)

        if date:
            target_article = server.day_to_post(group_name, server.days_old(date))
        else:
            target_article = server.day_to_post(group_name, config.site['backfill_days'])

        group = db.groups.find_one({'name': group_name})
        if group:
            # if the group hasn't been updated before, quit
            if not group['first']:
                log.error('{}: Need to run a normal update prior to backfilling group.'.format(group_name))
                return False

            log.info('{}: Server has {1:d} - {2:d} or ~{3:d} days.'
            .format(group_name, first, last, server.days_old(server.post_date(group_name, first)))
            )

            # if the first article we have is lower than the target
            if target_article >= group['first']:
                log.info('{}: Nothing to do, we already have the target post.'.format(group_name))
                return True

            # or if the target is below the server's first
            if target_article < first:
                log.warning(
                    '{}: Backfill target is older than the server\'s retention. Setting target to the first possible article.'.format(
                        group_name))
                target_article = first

            total = group['first'] - target_article
            end = group['first'] - 1
            start = end - MESSAGE_LIMIT + 1
            if target_article > start:
                start = target_article

            while True:
                messages = server.scan(group_name, start, end)
                if not messages:
                    log.error('{}: Could not scan group.'.format(group_name))
                    return False

                if parts.save_all(messages):
                    db.groups.update({
                                         '_id': group['_id']
                                     },
                                     {
                                         '$set': {
                                             'first': start
                                         }
                                     })
                    pass
                else:
                    log.error('{}: Failed while saving parts.'.format(group_name))
                    return False

                if first == target_article:
                    return True
                else:
                    end = start - 1
                    start = end - MESSAGE_LIMIT + 1
                    if target_article > start:
                        start = target_article

        else:
            log.error('{}: Group doesn\'t exist in db.'.format(group_name))
            return False


def update(group_name):
    log.info('{}: Updating group...'.format(group_name))

    with Server() as server:
        _, count, first, last, _ = server.group(group_name)

        group = db.groups.find_one({'name': group_name})
        if group:
            # if the group has been scanned before
            if group['last']:
                # pick up where we left off
                start = group['last'] + 1

                # if our last article is newer than the server's, something's wrong
                if last < group['last']:
                    log.error('{}: Server\'s last article {:d} is lower than the local {:d}'.format(group_name, last,
                                                                                                    group['last']))
                    return False
            else:
                # otherwise, start from x days old
                start = server.day_to_post(group_name, config.site['new_group_scan_days'])
                if not start:
                    log.error('{}: Couldn\'t determine a start point for group.'.format(group_name))
                    return False
                else:
                    db.groups.update({
                                         '_id': group['_id']
                                     },
                                     {
                                         '$set': {
                                             'first': start
                                         }
                                     })

            # either way, we're going upwards so end is the last available
            end = last

            # if total > 0, we have new parts
            total = end - start + 1

            log.debug('{}: Start: {:d} End: {:d} Total: {:d}'.format(group_name, start, end, total))
            if total > 0:
                if not group['last']:
                    log.info('{}: Starting new group with {:d} days and {:d} new parts.'
                    .format(group_name, config.site['new_group_scan_days'], total))
                else:
                    log.info('{}: Group has {:d} new parts.'.format(group_name, total))

                # until we're finished, loop
                while True:
                    # break the load into segments
                    if total > MESSAGE_LIMIT:
                        if start + MESSAGE_LIMIT > last:
                            end = last
                        else:
                            end = start + MESSAGE_LIMIT

                    messages = server.scan(group_name, start, end)
                    if not messages:
                        log.error('{}: Could not scan group.'.format(group_name))
                        return False

                    if parts.save_all(messages):
                        db.groups.update({
                                             '_id': group['_id']
                                         },
                                         {
                                             '$set': {
                                                 'last': end
                                             }
                                         })
                    else:
                        log.error('{}: Failed while saving parts.'.format(group_name))
                        return False

                    if end == last:
                        return True
                    else:
                        end = start + MESSAGE_LIMIT - 1
                        start = end + 1
                        log.info('{}: {:d} messages to go for this group.'.format(group_name, last - end))
            else:
                log.info('{}: No new records for group.'.format(group_name))
                return True
        else:
            log.error('{}: No such group exists in the db.'.format(group_name))
            return False