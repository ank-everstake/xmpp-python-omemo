#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
    This script uses Slixmpp OMEMO plugin
    Copyright (C) 2010  Nathanael C. Fritz
    Copyright (C) 2019 Maxime “pep” Buquet <pep@bouah.net>
 
    See the file LICENSE in Slixmpp OMEMO plugin repository for copying permission.
"""

import os
import sys
import asyncio
import logging

#from getpass import getpass
from argparse import ArgumentParser

from slixmpp import ClientXMPP, JID
from slixmpp.exceptions import IqTimeout, IqError
from slixmpp.stanza import Message
import slixmpp_omemo
from slixmpp_omemo import PluginCouldNotLoad, MissingOwnKey, EncryptionPrepareException
from slixmpp_omemo import UndecidedException, UntrustedException, NoAvailableSession
from omemo.exceptions import MissingBundleException

log = logging.getLogger(__name__)


class SendMsg(ClientXMPP):

    """
    A basic Slixmpp script that will log in, send a message,
    and then log out.

    python send_msg.py -t receiver@example.net -m "This is a message"

    """

    eme_ns = 'eu.siacs.conversations.axolotl'

    def __init__(self, jid, password, to, message):
        ClientXMPP.__init__(self, jid, password)

        self.to = to
        self.msg = message

        self.add_event_handler("session_start", self.start)

    def start(self, _event) -> None:
        """
        Process the session_start event.

        Typical actions for the session_start event are
        requesting the roster and broadcasting an initial
        presence stanza.

        Arguments:
            event -- An empty dictionary. The session_start
                     event does not provide any additional
                     data.
        """
        self.send_presence()
        self.get_roster()

        #self.send_message(mto=self.recipient, mbody=self.msg, mtype='chat')

        asyncio.ensure_future(self.encrypted_send())


    async def plain_send(self, message):
        """
        Helper to send to messages
        """

        msg = self.make_message(mto=self.to, mtype='chat')
        msg['body'] = message # self.msg
        return msg.send()

    async def encrypted_send(self):
        """Helper to send with encrypted messages"""

        msg = self.make_message(mto=self.to, mtype='chat')
        msg['eme']['namespace'] = self.eme_ns
        msg['eme']['name'] = self['xep_0380'].mechanisms[self.eme_ns]

        expect_problems = {}  # type: Optional[Dict[JID, List[int]]]

        while True:
            try:
                # `encrypt_message` excepts the plaintext to be sent, a list of
                # bare JIDs to encrypt to, and optionally a dict of problems to
                # expect per bare JID.
                #
                # Note that this function returns an `<encrypted/>` object,
                # and not a full Message stanza. This combined with the
                # `recipients` parameter that requires for a list of JIDs,
                # allows you to encrypt for 1:1 as well as groupchats (MUC).
                #
                # `expect_problems`: See EncryptionPrepareException handling.

                recipients = [JID(self.to)]
                encrypt = await self['xep_0384'].encrypt_message(self.msg, recipients, expect_problems)
                msg.append(encrypt)
                return msg.send()
            except UndecidedException as exn:
                # The library prevents us from sending a message to an
                # untrusted/undecided barejid, so we need to make a decision here.
                # This is where you prompt your user to ask what to do. In
                # this bot we will automatically trust undecided recipients.
                self['xep_0384'].trust(exn.bare_jid, exn.device, exn.ik)
            # TODO: catch NoEligibleDevicesException
            except EncryptionPrepareException as exn:
                # This exception is being raised when the library has tried
                # all it could and doesn't know what to do anymore. It
                # contains a list of exceptions that the user must resolve, or
                # explicitely ignore via `expect_problems`.
                # TODO: We might need to bail out here if errors are the same?
                for error in exn.errors:
                    if isinstance(error, MissingBundleException):
                        # We choose to ignore MissingBundleException. It seems
                        # to be somewhat accepted that it's better not to
                        # encrypt for a device if it has problems and encrypt
                        # for the rest, rather than error out. The "faulty"
                        # device won't be able to decrypt and should display a
                        # generic message. The receiving end-user at this
                        # point can bring up the issue if it happens.
                        self.plain_send('Could not find keys for device "%d" of recipient "%s". Skipping.' %
                            (error.device, error.bare_jid)
                        )
                        jid = JID(error.bare_jid)
                        device_list = expect_problems.setdefault(jid, [])
                        device_list.append(error.device)
            except (IqError, IqTimeout) as exn:
                self.plain_send('An error occured while fetching information on a recipient.\n%r' % exn)
                return None
            except Exception as exn:
                await self.plain_send('An error occured while attempting to encrypt.\n%r' % exn)
                raise

            finally:
                self.disconnect()

        return None


if __name__ == '__main__':
    # Setup the command line arguments.
    parser = ArgumentParser(description=SendMsg.__doc__)

    # Output verbosity options.
    parser.add_argument("-q", "--quiet", help="set logging to ERROR",
                        action="store_const", dest="loglevel",
                        const=logging.ERROR, default=logging.INFO)
    parser.add_argument("-d", "--debug", help="set logging to DEBUG",
                        action="store_const", dest="loglevel",
                        const=logging.DEBUG, default=logging.INFO)

    parser.add_argument("-t", "--to", dest="to",
                        help="JID to send the message to")
    parser.add_argument("-m", "--message", dest="message",
                        help="message to send")


    # Data dir for omemo plugin
    DATA_DIR = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        'omemo',
    )
    parser.add_argument("--data-dir", dest="data_dir",
                        help="data directory", default=DATA_DIR)

    args = parser.parse_args()

    # Setup logging.
    logging.basicConfig(level=args.loglevel,
                        format='%(levelname)-8s %(message)s')

    jid = "sender@example.com"
    password = "YOUR_STRONG_PASS"

    if args.to is None:
        args.to = input("Send To: ")
    if args.message is None:
        args.message = input("Message: ")

    # Setup the SendMsg and register plugins. Note that while plugins may
    # have interdependencies, the order in which you register them does
    # not matter.

    # Ensure OMEMO data dir is created
    os.makedirs(args.data_dir, exist_ok=True)

    xmpp = SendMsg(jid, password, args.to, args.message)

    xmpp.register_plugin('xep_0030') # Service Discovery
    xmpp.register_plugin('xep_0199') # XMPP Ping
    xmpp.register_plugin('xep_0380') # Explicit Message Encryption

    try:
        xmpp.register_plugin(
            'xep_0384',
            {
                'data_dir': args.data_dir,
            },
            module=slixmpp_omemo,
        ) # OMEMO
    except (PluginCouldNotLoad,):
        log.exception('And error occured when loading the omemo plugin.')
        sys.exit(1)

    # Connect to the XMPP server and start processing XMPP stanzas.
    xmpp.connect()
    xmpp.process(forever=False)
