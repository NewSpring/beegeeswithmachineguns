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

import bees
from urlparse import urlparse
from optparse import OptionParser, OptionGroup

def parse_options():
    """
    Handle the command line arguments for spinning up bees
    """
    parser = OptionParser(usage="""
bees COMMAND [options]

Bees with Machine Guns

A utility for arming (creating) many bees (small EC2 instances) to attack
(load test) targets (web applications).

commands:
  up      Start a batch of load testing servers.
  attack  Begin the attack on a specific url.
  down    Shutdown and deactivate the load testing servers.
  report  Report the status of the load testing servers.
    """)

    up_group = OptionGroup(parser, "up",
                           """In order to spin up new servers you will need to specify at least the -k command, which is the name of the EC2 keypair to use for creating and connecting to the new servers. The bees will expect to find a .pem file with this name in ~/.ssh/. Alternatively, bees can use SSH Agent for the key.""")

    # Required
    up_group.add_option('-k', '--key',  metavar="KEY",  nargs=1,
                        action='store', dest='key', type='string',
                        help="The ssh key pair name to use to connect to the new servers.")

    up_group.add_option('-s', '--servers', metavar="SERVERS", nargs=1,
                        action='store', dest='servers', type='int', default=5,
                        help="The number of servers to start (default: 5).")
    up_group.add_option('-g', '--group', metavar="GROUP", nargs=1,
                        action='store', dest='group', type='string', default='default',
                        help="The security group(s) to run the instances under (default: default).")
    up_group.add_option('-z', '--zone',  metavar="ZONE",  nargs=1,
                        action='store', dest='zone', type='string', default='us-east-1d',
                        help="The availability zone to start the instances in (default: us-east-1d).")
    up_group.add_option('-i', '--instance',  metavar="INSTANCE",  nargs=1,
                        action='store', dest='instance', type='string', default='ami-11e0597a',
                        help="The instance-id to use for each server from (default: ami-11e0597a).")
    up_group.add_option('-t', '--type',  metavar="TYPE",  nargs=1,
                        action='store', dest='type', type='string', default='t2.micro',
                        help="The instance-type to use for each server (default: t2.micro).")
    up_group.add_option('-l', '--login',  metavar="LOGIN",  nargs=1,
                        action='store', dest='login', type='string', default='ubuntu',
                        help="The ssh username name to use to connect to the new servers (default: ubuntu).")
    up_group.add_option('-v', '--subnet',  metavar="SUBNET",  nargs=1,
                        action='store', dest='subnet', type='string', default='subnet-fc8736a5',
                        help="The vpc subnet id in which the instances should be launched. (default: subnet-fc8736a5).")
    up_group.add_option('-b', '--bid', metavar="BID", nargs=1,
                        action='store', dest='bid', type='float', default=None,
                        help="The maximum bid price per spot instance (default: None).")

    parser.add_option_group(up_group)

    (options, args) = parser.parse_args()

    if len(args) <= 0:
        parser.error('Please enter a command.')

    command = args[0]

    if command == 'up':
        if not options.key:
            parser.error('To spin up new instances you need to specify a key-pair name with -k')

        if options.group == 'default':
            print 'New bees will use the "default" EC2 security group. Please note that port 22 (SSH) is not normally open on this group. You will need to use to the EC2 tools to open it before you will be able to attack.'

        bees.up(options.servers, options.group, options.zone, options.instance, options.type, options.login, options.key, options.subnet, options.bid)

    elif command == 'init':
        bees.init()
    elif command == 'attack':
        bees.attack()
    elif command == 'down':
        bees.down()
    elif command == 'report':
        bees.report()

def main():
    parse_options()
