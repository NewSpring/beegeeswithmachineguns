#!/bin/env python

"""
The MIT License

Copyright (c) 2010 The Chicago Tribune & Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""

from multiprocessing import Pool
import os
import re
import socket
import time
import urllib
import urllib2
import base64
import csv
import sys
import random
import ssl
import httplib
import json

import boto
import boto.ec2
import paramiko

STATE_FILENAME = os.path.expanduser('~/.bees')

class bcolors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
# Utilities

def _read_server_list():
    instance_ids = []

    if not os.path.isfile(STATE_FILENAME):
        return (None, None, None, None)

    with open(STATE_FILENAME, 'r') as f:
        username = f.readline().strip()
        key_name = f.readline().strip()
        zone = f.readline().strip()
        text = f.read()
        instance_ids = [i for i in text.split('\n') if i != '']

        print 'Read %i bees from the roster.' % len(instance_ids)

    return (username, key_name, zone, instance_ids)

def _write_server_list(username, key_name, zone, instances):
    with open(STATE_FILENAME, 'w') as f:
        f.write('%s\n' % username)
        f.write('%s\n' % key_name)
        f.write('%s\n' % zone)
        f.write('\n'.join([instance.id for instance in instances]))

def _delete_server_list():
    os.remove(STATE_FILENAME)

def _get_pem_path(key):
    return os.path.expanduser('~/.ssh/%s.pem' % key)

def _get_region(zone):
    return zone if 'gov' in zone else zone[:-1] # chop off the "d" in the "us-east-1d" to get the "Region"

def _get_security_group_ids(connection, security_group_names, subnet):
    ids = []
    # Since we cannot get security groups in a vpc by name, we get all security groups and parse them by name later
    security_groups = connection.get_all_security_groups()

    # Parse the name of each security group and add the id of any match to the group list
    for group in security_groups:
        for name in security_group_names:
            if group.name == name:
                if subnet == None:
                    if group.vpc_id == None:
                        ids.append(group.id)
                    elif group.vpc_id != None:
                        ids.append(group.id)

        return ids

# Methods

def up(count, group, zone, image_id, instance_type, username, key_name, subnet, bid = None):
    """
    Startup the load testing server.
    """

    existing_username, existing_key_name, existing_zone, instance_ids = _read_server_list()

    count = int(count)
    if existing_username == username and existing_key_name == key_name and existing_zone == zone:
        # User, key and zone match existing values and instance ids are found on state file
        if count <= len(instance_ids):
            # Count is less than the amount of existing instances. No need to create new ones.
            print 'Bees are already assembled and awaiting orders.'
            return
        else:
            # Count is greater than the amount of existing instances. Need to create the only the extra instances.
            count -= len(instance_ids)
    elif instance_ids:
        # Instances found on state file but user, key and/or zone not matching existing value.
        # State file only stores one user/key/zone config combination so instances are unusable.
        print 'Taking down {} unusable bees.'.format(len(instance_ids))
        # Redirect prints in down() to devnull to avoid duplicate messages
        _redirect_stdout('/dev/null', down)
        # down() deletes existing state file so _read_server_list() returns a blank state
        existing_username, existing_key_name, existing_zone, instance_ids = _read_server_list()

    pem_path = _get_pem_path(key_name)

    if not os.path.isfile(pem_path):
        print 'Warning. No key file found for %s. You will need to add this key to your SSH agent to connect.' % pem_path

    print 'Connecting to the hive.'

    try:
        ec2_connection = boto.ec2.connect_to_region(_get_region(zone))
    except boto.exception.NoAuthHandlerFound as e:
        print "Authenciation config error, perhaps you do not have a ~/.boto file with correct permissions?"
        print e.message
        return e
    except Exception as e:
        print "Unknown error occured:"
        print e.message
        return e

    if ec2_connection == None:
        raise Exception("Invalid zone specified? Unable to connect to region using zone name")

    if bid:
        print 'Attempting to call up %i spot bees, this can take a while...' % count

        spot_requests = ec2_connection.request_spot_instances(
            image_id=image_id,
            price=bid,
            count=count,
            key_name=key_name,
            security_groups=[group] if subnet is None else _get_security_group_ids(ec2_connection, [group], subnet),
            instance_type=instance_type,
            placement=None if 'gov' in zone else zone,
            subnet_id=subnet)

        # it can take a few seconds before the spot requests are fully processed
        time.sleep(5)

        instances = _wait_for_spot_request_fulfillment(ec2_connection, spot_requests)
    else:
        print 'Attempting to call up %i bees.' % count

        try:
            reservation = ec2_connection.run_instances(
                image_id=image_id,
                min_count=count,
                max_count=count,
                key_name=key_name,
                security_groups=[group] if subnet is None else _get_security_group_ids(ec2_connection, [group], subnet),
                instance_type=instance_type,
                placement=None if 'gov' in zone else zone,
                subnet_id=subnet)
        except boto.exception.EC2ResponseError as e:
            print "Unable to call bees:", e.message
            return e

        instances = reservation.instances

    if instance_ids:
        existing_reservations = ec2_connection.get_all_instances(instance_ids=instance_ids)
        existing_instances = [r.instances[0] for r in existing_reservations]
        map(instances.append, existing_instances)

    print 'Waiting for bees to load their machine guns...'

    instance_ids = instance_ids or []

    for instance in filter(lambda i: i.state == 'pending', instances):
        instance.update()
        while instance.state != 'running':
            print '.'
            time.sleep(5)
            instance.update()

        instance_ids.append(instance.id)

        print 'Bee %s is ready for the attack.' % instance.id

    ec2_connection.create_tags(instance_ids, { "Name": "a bee!" })

    _write_server_list(username, key_name, zone, instances)

    print 'The swarm has assembled %i bees.' % len(instances)

def report():
    """
    Report the status of the load testing servers.
    """
    username, key_name, zone, instance_ids = _read_server_list()

    if not instance_ids:
        print 'No bees have been mobilized.'
        return

    ec2_connection = boto.ec2.connect_to_region(_get_region(zone))

    reservations = ec2_connection.get_all_instances(instance_ids=instance_ids)

    instances = []

    for reservation in reservations:
        instances.extend(reservation.instances)

    for instance in instances:
        print 'Bee %s: %s @ %s' % (instance.id, instance.state, instance.private_ip_address)

def down():
    """
    Shutdown the load testing server.
    """
    username, key_name, zone, instance_ids = _read_server_list()

    if not instance_ids:
        print 'No bees have been mobilized.'
        return

    print 'Connecting to the hive.'

    ec2_connection = boto.ec2.connect_to_region(_get_region(zone))

    print 'Calling off the swarm.'

    terminated_instance_ids = ec2_connection.terminate_instances(
        instance_ids=instance_ids)

    print 'Stood down %i bees.' % len(terminated_instance_ids)

    _delete_server_list()

def init():
    """
    Initalize the servers.
    """
    print 'Training the bees.'

    username, key_name, zone, instance_ids = _read_server_list()

    if not instance_ids:
        print 'No bees are ready to attack.'
        return

    print 'Connecting to the hive.'

    ec2_connection = boto.ec2.connect_to_region(_get_region(zone))

    print 'Assembling bees.'

    reservations = ec2_connection.get_all_instances(instance_ids=instance_ids)

    instances = []

    for reservation in reservations:
        instances.extend(reservation.instances)

    params = []

    for i, instance in enumerate(instances):
        params.append({
            'i': i,
            'instance_id': instance.id,
            'instance_name': instance.private_dns_name if instance.public_dns_name == "" else instance.public_dns_name,
            'username': username,
            'key_name': key_name
        })

    pool = Pool(len(params))
    pool.map(_init, params)
    return

def damage():
    test_string = """
[
    {
        "stats": {
            "suites": 6,
            "tests": 20,
            "passes": 17,
            "pending": 0,
            "failures": 3,
            "start": "2015-08-26T17:29:17.994Z",
            "end": "2015-08-26T17:31:30.351Z",
            "duration": 132357
        },
        "tests": [
            {
                "title": "should load the admin page",
                "fullTitle": "Checkin Testing should load the admin page",
                "duration": 46
            },
            {
                "title": "should select ministry areas to check in to",
                "fullTitle": "Checkin Testing should select ministry areas to check in to",
                "duration": 24
            },
            {
                "title": "should go to the search screen",
                "fullTitle": "Checkin Testing should go to the search screen",
                "duration": 4181
            },
            {
                "title": "should search for someone",
                "fullTitle": "Checkin Testing should search for someone",
                "duration": 604
            },
            {
                "title": "should return results of serach",
                "fullTitle": "Checkin Testing should return results of serach",
                "duration": 8
            },
            {
                "title": "should have people selected as active",
                "fullTitle": "Checkin Testing has results should have people selected as active",
                "duration": 1
            },
            {
                "title": "should go to the print screen and print",
                "fullTitle": "Checkin Testing has results should go to the print screen and print",
                "duration": 798
            },
            {
                "title": "should print",
                "fullTitle": "Checkin Testing has results should print",
                "duration": 1
            },
            {
                "title": "should go back to search",
                "fullTitle": "Checkin Testing has results should go back to search",
                "duration": 296
            },
            {
                "title": "should have a message about no one found",
                "fullTitle": "Checkin Testing no results should have a message about no one found",
                "duration": 1
            },
            {
                "title": "should load the admin page",
                "fullTitle": "Checkin Testing should load the admin page",
                "duration": 0
            },
            {
                "title": "should select ministry areas to check in to",
                "fullTitle": "Checkin Testing should select ministry areas to check in to",
                "duration": 10010
            },
            {
                "title": "should go to the search screen",
                "fullTitle": "Checkin Testing should go to the search screen",
                "duration": 187
            },
            {
                "title": "should search for someone",
                "fullTitle": "Checkin Testing should search for someone",
                "duration": 30002
            },
            {
                "title": "should return results of serach",
                "fullTitle": "Checkin Testing should return results of serach",
                "duration": 30001
            },
            {
                "title": "should have people selected as active",
                "fullTitle": "Checkin Testing has results should have people selected as active",
                "duration": 1
            },
            {
                "title": "should go to the print screen and print",
                "fullTitle": "Checkin Testing has results should go to the print screen and print",
                "duration": 0
            },
            {
                "title": "should print",
                "fullTitle": "Checkin Testing has results should print",
                "duration": 1
            },
            {
                "title": "should go back to search",
                "fullTitle": "Checkin Testing has results should go back to search",
                "duration": 0
            },
            {
                "title": "should have a message about no one found",
                "fullTitle": "Checkin Testing no results should have a message about no one found",
                "duration": 30001
            }
        ],
        "failures": [
            {
                "title": "should search for someone",
                "fullTitle": "Checkin Testing should search for someone",
                "duration": 30002
            },
            {
                "title": "should return results of serach",
                "fullTitle": "Checkin Testing should return results of serach",
                "duration": 30001
            },
            {
                "title": "should have a message about no one found",
                "fullTitle": "Checkin Testing no results should have a message about no one found",
                "duration": 30001
            }
        ],
        "passes": [
            {
                "title": "should load the admin page",
                "fullTitle": "Checkin Testing should load the admin page",
                "duration": 46
            },
            {
                "title": "should select ministry areas to check in to",
                "fullTitle": "Checkin Testing should select ministry areas to check in to",
                "duration": 24
            },
            {
                "title": "should go to the search screen",
                "fullTitle": "Checkin Testing should go to the search screen",
                "duration": 4181
            },
            {
                "title": "should search for someone",
                "fullTitle": "Checkin Testing should search for someone",
                "duration": 604
            },
            {
                "title": "should return results of serach",
                "fullTitle": "Checkin Testing should return results of serach",
                "duration": 8
            },
            {
                "title": "should have people selected as active",
                "fullTitle": "Checkin Testing has results should have people selected as active",
                "duration": 1
            },
            {
                "title": "should go to the print screen and print",
                "fullTitle": "Checkin Testing has results should go to the print screen and print",
                "duration": 798
            },
            {
                "title": "should print",
                "fullTitle": "Checkin Testing has results should print",
                "duration": 1
            },
            {
                "title": "should go back to search",
                "fullTitle": "Checkin Testing has results should go back to search",
                "duration": 296
            },
            {
                "title": "should have a message about no one found",
                "fullTitle": "Checkin Testing no results should have a message about no one found",
                "duration": 1
            },
            {
                "title": "should load the admin page",
                "fullTitle": "Checkin Testing should load the admin page",
                "duration": 0
            },
            {
                "title": "should select ministry areas to check in to",
                "fullTitle": "Checkin Testing should select ministry areas to check in to",
                "duration": 10010
            },
            {
                "title": "should go to the search screen",
                "fullTitle": "Checkin Testing should go to the search screen",
                "duration": 187
            },
            {
                "title": "should have people selected as active",
                "fullTitle": "Checkin Testing has results should have people selected as active",
                "duration": 1
            },
            {
                "title": "should go to the print screen and print",
                "fullTitle": "Checkin Testing has results should go to the print screen and print",
                "duration": 0
            },
            {
                "title": "should print",
                "fullTitle": "Checkin Testing has results should print",
                "duration": 1
            },
            {
                "title": "should go back to search",
                "fullTitle": "Checkin Testing has results should go back to search",
                "duration": 0
            }
        ]
    },
    {
        "stats": {
            "suites": 6,
            "tests": 20,
            "passes": 17,
            "pending": 0,
            "failures": 3,
            "start": "2015-08-26T17:29:17.994Z",
            "end": "2015-08-26T17:31:30.351Z",
            "duration": 132357
        },
        "tests": [
            {
                "title": "should load the admin page",
                "fullTitle": "Checkin Testing should load the admin page",
                "duration": 46
            },
            {
                "title": "should select ministry areas to check in to",
                "fullTitle": "Checkin Testing should select ministry areas to check in to",
                "duration": 24
            },
            {
                "title": "should go to the search screen",
                "fullTitle": "Checkin Testing should go to the search screen",
                "duration": 4181
            },
            {
                "title": "should search for someone",
                "fullTitle": "Checkin Testing should search for someone",
                "duration": 604
            },
            {
                "title": "should return results of serach",
                "fullTitle": "Checkin Testing should return results of serach",
                "duration": 8
            },
            {
                "title": "should have people selected as active",
                "fullTitle": "Checkin Testing has results should have people selected as active",
                "duration": 1
            },
            {
                "title": "should go to the print screen and print",
                "fullTitle": "Checkin Testing has results should go to the print screen and print",
                "duration": 798
            },
            {
                "title": "should print",
                "fullTitle": "Checkin Testing has results should print",
                "duration": 1
            },
            {
                "title": "should go back to search",
                "fullTitle": "Checkin Testing has results should go back to search",
                "duration": 296
            },
            {
                "title": "should have a message about no one found",
                "fullTitle": "Checkin Testing no results should have a message about no one found",
                "duration": 1
            },
            {
                "title": "should load the admin page",
                "fullTitle": "Checkin Testing should load the admin page",
                "duration": 0
            },
            {
                "title": "should select ministry areas to check in to",
                "fullTitle": "Checkin Testing should select ministry areas to check in to",
                "duration": 10010
            },
            {
                "title": "should go to the search screen",
                "fullTitle": "Checkin Testing should go to the search screen",
                "duration": 187
            },
            {
                "title": "should search for someone",
                "fullTitle": "Checkin Testing should search for someone",
                "duration": 30002
            },
            {
                "title": "should return results of serach",
                "fullTitle": "Checkin Testing should return results of serach",
                "duration": 30001
            },
            {
                "title": "should have people selected as active",
                "fullTitle": "Checkin Testing has results should have people selected as active",
                "duration": 1
            },
            {
                "title": "should go to the print screen and print",
                "fullTitle": "Checkin Testing has results should go to the print screen and print",
                "duration": 0
            },
            {
                "title": "should print",
                "fullTitle": "Checkin Testing has results should print",
                "duration": 1
            },
            {
                "title": "should go back to search",
                "fullTitle": "Checkin Testing has results should go back to search",
                "duration": 0
            },
            {
                "title": "should have a message about no one found",
                "fullTitle": "Checkin Testing no results should have a message about no one found",
                "duration": 30001
            }
        ],
        "failures": [
            {
                "title": "should search for someone",
                "fullTitle": "Checkin Testing should search for someone",
                "duration": 30002
            },
            {
                "title": "should return results of serach",
                "fullTitle": "Checkin Testing should return results of serach",
                "duration": 30001
            },
            {
                "title": "should have a message about no one found",
                "fullTitle": "Checkin Testing no results should have a message about no one found",
                "duration": 30001
            }
        ],
        "passes": [
            {
                "title": "should load the admin page",
                "fullTitle": "Checkin Testing should load the admin page",
                "duration": 46
            },
            {
                "title": "should select ministry areas to check in to",
                "fullTitle": "Checkin Testing should select ministry areas to check in to",
                "duration": 24
            },
            {
                "title": "should go to the search screen",
                "fullTitle": "Checkin Testing should go to the search screen",
                "duration": 4181
            },
            {
                "title": "should search for someone",
                "fullTitle": "Checkin Testing should search for someone",
                "duration": 604
            },
            {
                "title": "should return results of serach",
                "fullTitle": "Checkin Testing should return results of serach",
                "duration": 8
            },
            {
                "title": "should have people selected as active",
                "fullTitle": "Checkin Testing has results should have people selected as active",
                "duration": 1
            },
            {
                "title": "should go to the print screen and print",
                "fullTitle": "Checkin Testing has results should go to the print screen and print",
                "duration": 798
            },
            {
                "title": "should print",
                "fullTitle": "Checkin Testing has results should print",
                "duration": 1
            },
            {
                "title": "should go back to search",
                "fullTitle": "Checkin Testing has results should go back to search",
                "duration": 296
            },
            {
                "title": "should have a message about no one found",
                "fullTitle": "Checkin Testing no results should have a message about no one found",
                "duration": 1
            },
            {
                "title": "should load the admin page",
                "fullTitle": "Checkin Testing should load the admin page",
                "duration": 0
            },
            {
                "title": "should select ministry areas to check in to",
                "fullTitle": "Checkin Testing should select ministry areas to check in to",
                "duration": 10010
            },
            {
                "title": "should go to the search screen",
                "fullTitle": "Checkin Testing should go to the search screen",
                "duration": 187
            },
            {
                "title": "should have people selected as active",
                "fullTitle": "Checkin Testing has results should have people selected as active",
                "duration": 1
            },
            {
                "title": "should go to the print screen and print",
                "fullTitle": "Checkin Testing has results should go to the print screen and print",
                "duration": 0
            },
            {
                "title": "should print",
                "fullTitle": "Checkin Testing has results should print",
                "duration": 1
            },
            {
                "title": "should go back to search",
                "fullTitle": "Checkin Testing has results should go back to search",
                "duration": 0
            }
        ]
    },
    {
        "stats": {
            "suites": 6,
            "tests": 20,
            "passes": 17,
            "pending": 0,
            "failures": 3,
            "start": "2015-08-26T17:29:17.994Z",
            "end": "2015-08-26T17:31:30.351Z",
            "duration": 132357
        },
        "tests": [
            {
                "title": "should load the admin page",
                "fullTitle": "Checkin Testing should load the admin page",
                "duration": 46
            },
            {
                "title": "should select ministry areas to check in to",
                "fullTitle": "Checkin Testing should select ministry areas to check in to",
                "duration": 24
            },
            {
                "title": "should go to the search screen",
                "fullTitle": "Checkin Testing should go to the search screen",
                "duration": 4181
            },
            {
                "title": "should search for someone",
                "fullTitle": "Checkin Testing should search for someone",
                "duration": 604
            },
            {
                "title": "should return results of serach",
                "fullTitle": "Checkin Testing should return results of serach",
                "duration": 8
            },
            {
                "title": "should have people selected as active",
                "fullTitle": "Checkin Testing has results should have people selected as active",
                "duration": 1
            },
            {
                "title": "should go to the print screen and print",
                "fullTitle": "Checkin Testing has results should go to the print screen and print",
                "duration": 798
            },
            {
                "title": "should print",
                "fullTitle": "Checkin Testing has results should print",
                "duration": 1
            },
            {
                "title": "should go back to search",
                "fullTitle": "Checkin Testing has results should go back to search",
                "duration": 296
            },
            {
                "title": "should have a message about no one found",
                "fullTitle": "Checkin Testing no results should have a message about no one found",
                "duration": 1
            },
            {
                "title": "should load the admin page",
                "fullTitle": "Checkin Testing should load the admin page",
                "duration": 0
            },
            {
                "title": "should select ministry areas to check in to",
                "fullTitle": "Checkin Testing should select ministry areas to check in to",
                "duration": 10010
            },
            {
                "title": "should go to the search screen",
                "fullTitle": "Checkin Testing should go to the search screen",
                "duration": 187
            },
            {
                "title": "should search for someone",
                "fullTitle": "Checkin Testing should search for someone",
                "duration": 30002
            },
            {
                "title": "should return results of serach",
                "fullTitle": "Checkin Testing should return results of serach",
                "duration": 30001
            },
            {
                "title": "should have people selected as active",
                "fullTitle": "Checkin Testing has results should have people selected as active",
                "duration": 1
            },
            {
                "title": "should go to the print screen and print",
                "fullTitle": "Checkin Testing has results should go to the print screen and print",
                "duration": 0
            },
            {
                "title": "should print",
                "fullTitle": "Checkin Testing has results should print",
                "duration": 1
            },
            {
                "title": "should go back to search",
                "fullTitle": "Checkin Testing has results should go back to search",
                "duration": 0
            },
            {
                "title": "should have a message about no one found",
                "fullTitle": "Checkin Testing no results should have a message about no one found",
                "duration": 30001
            }
        ],
        "failures": [
            {
                "title": "should search for someone",
                "fullTitle": "Checkin Testing should search for someone",
                "duration": 30002
            },
            {
                "title": "should return results of serach",
                "fullTitle": "Checkin Testing should return results of serach",
                "duration": 30001
            },
            {
                "title": "should have a message about no one found",
                "fullTitle": "Checkin Testing no results should have a message about no one found",
                "duration": 30001
            }
        ],
        "passes": [
            {
                "title": "should load the admin page",
                "fullTitle": "Checkin Testing should load the admin page",
                "duration": 46
            },
            {
                "title": "should select ministry areas to check in to",
                "fullTitle": "Checkin Testing should select ministry areas to check in to",
                "duration": 24
            },
            {
                "title": "should go to the search screen",
                "fullTitle": "Checkin Testing should go to the search screen",
                "duration": 4181
            },
            {
                "title": "should search for someone",
                "fullTitle": "Checkin Testing should search for someone",
                "duration": 604
            },
            {
                "title": "should return results of serach",
                "fullTitle": "Checkin Testing should return results of serach",
                "duration": 8
            },
            {
                "title": "should have people selected as active",
                "fullTitle": "Checkin Testing has results should have people selected as active",
                "duration": 1
            },
            {
                "title": "should go to the print screen and print",
                "fullTitle": "Checkin Testing has results should go to the print screen and print",
                "duration": 798
            },
            {
                "title": "should print",
                "fullTitle": "Checkin Testing has results should print",
                "duration": 1
            },
            {
                "title": "should go back to search",
                "fullTitle": "Checkin Testing has results should go back to search",
                "duration": 296
            },
            {
                "title": "should have a message about no one found",
                "fullTitle": "Checkin Testing no results should have a message about no one found",
                "duration": 1
            },
            {
                "title": "should load the admin page",
                "fullTitle": "Checkin Testing should load the admin page",
                "duration": 0
            },
            {
                "title": "should select ministry areas to check in to",
                "fullTitle": "Checkin Testing should select ministry areas to check in to",
                "duration": 10010
            },
            {
                "title": "should go to the search screen",
                "fullTitle": "Checkin Testing should go to the search screen",
                "duration": 187
            },
            {
                "title": "should have people selected as active",
                "fullTitle": "Checkin Testing has results should have people selected as active",
                "duration": 1
            },
            {
                "title": "should go to the print screen and print",
                "fullTitle": "Checkin Testing has results should go to the print screen and print",
                "duration": 0
            },
            {
                "title": "should print",
                "fullTitle": "Checkin Testing has results should print",
                "duration": 1
            },
            {
                "title": "should go back to search",
                "fullTitle": "Checkin Testing has results should go back to search",
                "duration": 0
            }
        ]
    }
]
    """

    _print_results(test_string)

def _init(params):
    """
    Run the init on each server
    """
    print 'Bee %i is gon\' learn today.' % params['i']

    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        pem_path = params.get('key_name') and _get_pem_path(params['key_name']) or None
        if not os.path.isfile(pem_path):
            client.load_system_host_keys()
            client.connect(params['instance_name'], username=params['username'])
        else:
            client.connect(
                params['instance_name'],
                username=params['username'],
                key_filename=pem_path)

        # clone down the repo
        init_command = 'rm -rf checkin-test && git clone https://github.com/NewSpring/checkin-test && cd checkin-test && npm i'
        stdin, stdout, stderr = client.exec_command(init_command)

        init_results = stdout.read()
        init_error = stderr.read()

        if 'fatal' in init_error:
            print 'Bee %i is above this.' % params['i']
        else:
            print 'Bee %i done learned.' % params['i']


        # install dependencies
        # install_command = 'cd checkin-test && npm

        return init_results
    except socket.error, e:
        return e


def _wait_for_spot_request_fulfillment(conn, requests, fulfilled_requests = []):
    """
    Wait until all spot requests are fulfilled.

    Once all spot requests are fulfilled, return a list of corresponding spot instances.
    """
    if len(requests) == 0:
        reservations = conn.get_all_instances(instance_ids = [r.instance_id for r in fulfilled_requests])
        return [r.instances[0] for r in reservations]
    else:
        time.sleep(10)
        print '.'

    requests = conn.get_all_spot_instance_requests(request_ids=[req.id for req in requests])
    for req in requests:
        if req.status.code == 'fulfilled':
            fulfilled_requests.append(req)
            print "spot bee `{}` joined the swarm.".format(req.instance_id)

    return _wait_for_spot_request_fulfillment(conn, [r for r in requests if r not in fulfilled_requests], fulfilled_requests)

def _attack(params):
    """
    Test the target URL with requests.

    Intended for use with multiprocessing.
    """
    print 'Bee %i is joining the swarm.' % params['i']

    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        pem_path = params.get('key_name') and _get_pem_path(params['key_name']) or None
        if not os.path.isfile(pem_path):
            client.load_system_host_keys()
            client.connect(params['instance_name'], username=params['username'])
        else:
            client.connect(
                params['instance_name'],
                username=params['username'],
                key_filename=pem_path)

        print 'Bee %i is firing her machine gun. Bang bang!' % params['i']

        options = ''
        if params['headers'] is not '':
            for h in params['headers'].split(';'):
                if h != '':
                    options += ' -H "%s"' % h.strip()

        stdin, stdout, stderr = client.exec_command('mktemp')
        params['csv_filename'] = stdout.read().strip()
        if params['csv_filename']:
            options += ' -e %(csv_filename)s' % params
        else:
            print 'Bee %i lost sight of the target (connection timed out creating csv_filename).' % params['i']
            return None

        if params['post_file']:
            pem_file_path=_get_pem_path(params['key_name'])
            os.system("scp -q -o 'StrictHostKeyChecking=no' -i %s %s %s@%s:/tmp/honeycomb" % (pem_file_path, params['post_file'], params['username'], params['instance_name']))
            options += ' -T "%(mime_type)s; charset=UTF-8" -p /tmp/honeycomb' % params

        if params['keep_alive']:
            options += ' -k'

        if params['cookies'] is not '':
            options += ' -H \"Cookie: %s;sessionid=NotARealSessionID;\"' % params['cookies']
        else:
            options += ' -C \"sessionid=NotARealSessionID\"'

        if params['basic_auth'] is not '':
            options += ' -A %s' % params['basic_auth']

        params['options'] = options

        test_command = 'cd checkin-test && export PATH=$PATH:/home/ubuntu/npm/bin && export NODE_PATH=$NODE_PATH:/home/ubuntu/npm/lib/node_modules && npm run attack'
        stdin, stdout, stderr = client.exec_command(test_command)

        test_results = stdout.read()
        test_error = stderr.read()

        print test_results
        print test_error

        return test_results

    except socket.error, e:
        return e


def attack(url, n, c, **options):
    """
    Test the root url of this site.
    """
    username, key_name, zone, instance_ids = _read_server_list()
    headers = options.get('headers', '')
    csv_filename = options.get("csv_filename", '')
    cookies = options.get('cookies', '')
    post_file = options.get('post_file', '')
    keep_alive = options.get('keep_alive', False)
    basic_auth = options.get('basic_auth', '')

    if csv_filename:
        try:
            stream = open(csv_filename, 'w')
        except IOError, e:
            raise IOError("Specified csv_filename='%s' is not writable. Check permissions or specify a different filename and try again." % csv_filename)

    if not instance_ids:
        print 'No bees are ready to attack.'
        return

    print 'Connecting to the hive.'

    ec2_connection = boto.ec2.connect_to_region(_get_region(zone))

    print 'Assembling bees.'

    reservations = ec2_connection.get_all_instances(instance_ids=instance_ids)

    instances = []

    for reservation in reservations:
        instances.extend(reservation.instances)

    instance_count = len(instances)

    if n < instance_count * 2:
        print 'bees: error: the total number of requests must be at least %d (2x num. instances)' % (instance_count * 2)
        return
    if c < instance_count:
        print 'bees: error: the number of concurrent requests must be at least %d (num. instances)' % instance_count
        return
    if n < c:
        print 'bees: error: the number of concurrent requests (%d) must be at most the same as number of requests (%d)' % (c, n)
        return

    requests_per_instance = int(float(n) / instance_count)
    connections_per_instance = int(float(c) / instance_count)

    print 'Each of %i bees will fire %s rounds, %s at a time.' % (instance_count, requests_per_instance, connections_per_instance)

    params = []

    for i, instance in enumerate(instances):
        params.append({
            'i': i,
            'instance_id': instance.id,
            'instance_name': instance.private_dns_name if instance.public_dns_name == "" else instance.public_dns_name,
            'url': url,
            'concurrent_requests': connections_per_instance,
            'num_requests': requests_per_instance,
            'username': username,
            'key_name': key_name,
            'headers': headers,
            'cookies': cookies,
            'post_file': options.get('post_file'),
            'keep_alive': options.get('keep_alive'),
            'mime_type': options.get('mime_type', ''),
            'tpr': options.get('tpr'),
            'rps': options.get('rps'),
            'basic_auth': options.get('basic_auth')
        })

    # Need to revisit to support all http verbs.
    if post_file:
        try:
            with open(post_file, 'r') as content_file:
                content = content_file.read()
            request.add_data(content)
        except IOError:
            print 'bees: error: The post file you provided doesn\'t exist.'
            return

    if cookies is not '':
        request.add_header('Cookie', cookies)

    if basic_auth is not '':
        authentication = base64.encodestring(basic_auth).replace('\n', '')
        request.add_header('Authorization', 'Basic %s' % authentication)

    print 'Organizing the swarm.'
    # Spin up processes for connecting to EC2 instances
    pool = Pool(len(params))
    results = pool.map(_attack, params)

    print 'Offensive complete.'

    print 'The swarm is awaiting new orders.'


def _redirect_stdout(outfile, func, *args, **kwargs):
    save_out = sys.stdout
    with open(outfile, 'w') as redir_out:
        sys.stdout = redir_out
        func(*args, **kwargs)
    sys.stdout = save_out

def _print_results(results):
    parsed = json.loads(results)

    tests = 0
    passes = 0
    failures = 0
    duration = 0

    failed_tests = []

    for result in parsed:
        stats = result['stats']

        tests += int(stats['tests'])
        passes += int(stats['passes'])
        failures += int(stats['failures'])
        duration += float(stats['duration'])

        for failed_test in result['failures']:
            failed_tests.append([failed_test['title'], failed_test['duration']])

    print '     Tests ran:      %i' % tests
    print '         Successful: %i' % passes
    print bcolors.FAIL + '         Failed:     %i' % failures + bcolors.ENDC
    print '         Duration:   %f seconds' % (float(duration) / 1000)
    print '     ==================================='
    print '     Failures:'
    for failed_test in failed_tests:
        print bcolors.FAIL + '          %s' % failed_test[0] + ' [%i' % failed_test[1] + ' ms]' + bcolors.ENDC
