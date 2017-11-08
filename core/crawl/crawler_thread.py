# -*- coding: utf-8 -*-

"""
HTCAP - beta 1
Author: filippo.cavallarin@wearesegment.com

This program is free software; you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation; either version 2 of the License, or (at your option) any later
version.
"""

from __future__ import unicode_literals

import json
import os
import tempfile
import threading
import uuid
from time import sleep

from core.constants import *
from core.crawl.lib.crawl_result import CrawlResult
from core.crawl.lib.probe import Probe
from core.crawl.lib.shared import Shared
from core.crawl.lib.utils import adjust_requests
from core.lib.exception import ThreadExitRequestException
from core.lib.http_get import HttpGet
from core.lib.shell import CommandExecutor
from core.lib.thirdparty.simhash import Simhash


# TODO: use NamedTemporaryFile for self._cookie_file
# from core.lib.utils import cmd_to_str


class CrawlerThread(threading.Thread):
    _PROCESS_RETRIES_INTERVAL = 0.5
    _PROCESS_RETRIES = 2

    def __init__(self):
        threading.Thread.__init__(self)

        self.status = THSTAT_RUNNING
        self.exit = False

        self._thread_uuid = uuid.uuid4()
        self._cookie_file = "%s%shtcap_cookiefile-%s.json" % (tempfile.gettempdir(), os.sep, self._thread_uuid)

    def run(self):
        self._crawl()

    def _crawl(self):

        while True:
            requests = []
            errors = []

            try:
                request = self._wait_request()
            except ThreadExitRequestException:
                if os.path.exists(self._cookie_file):
                    os.remove(self._cookie_file)
                return
            except Exception as e:
                print("-->" + str(e))
                continue

            probe = self._send_probe(request, errors)

            if probe:
                if probe.status == "ok" or probe.errcode == ERROR_PROBE_TO:

                    # Main condition for duplicate checking
                    if not (Shared.block_duplicates and self._is_duplicate(probe, request)):

                        requests = probe.requests

                        if len(probe.user_output) > 0:
                            request.user_output = probe.user_output

                        # if the probe return some cookies set it as the last one
                        if probe.cookies:
                            Shared.end_cookies = probe.cookies

            else:
                errors.append(ERROR_PROBEFAILURE)
                # get urls with python to continue crawling
                if not Shared.options['use_urllib_onerror']:
                    continue
                try:
                    hr = HttpGet(request, Shared.options['process_timeout'], CrawlerThread._PROCESS_RETRIES,
                                 Shared.options['user_agent'], Shared.options['proxy'])
                    requests = hr.get_requests()

                except Exception as e:
                    errors.append(str(e))

            # set out_of_scope, apply user-supplied filters to urls (ie group_qs)
            adjust_requests(requests)

            Shared.main_condition.acquire()
            res = CrawlResult(request, requests, errors)
            Shared.crawl_results.append(res)
            Shared.main_condition.notify()
            Shared.main_condition.release()

    def _wait_request(self):
        Shared.th_condition.acquire()
        while True:
            if self.exit:
                Shared.th_condition.notifyAll()
                Shared.th_condition.release()
                raise ThreadExitRequestException("exit request received")

            if Shared.requests_index >= len(Shared.requests):
                self.status = THSTAT_WAITING
                # The wait method releases the lock, blocks the current thread until another thread calls notify
                Shared.th_condition.wait()
                continue

            request = Shared.requests[Shared.requests_index]
            Shared.requests_index += 1

            break

        Shared.th_condition.release()

        self.status = THSTAT_RUNNING

        return request

    def _set_probe_params(self, request):
        params = []
        cookies = []
        url = request.url

        if request.method == "POST":
            params.append("-P")
            if request.data:
                params.extend(("-D", request.data))

        if len(request.cookies) > 0:
            for cookie in request.cookies:
                cookies.append(cookie.get_dict())
            with open(self._cookie_file, 'w') as fil:
                fil.write(json.dumps(cookies))
            params.extend(("-c", self._cookie_file))

        if request.http_auth:
            params.extend(("-p", request.http_auth))

        if Shared.options['set_referer'] and request.referer:
            params.extend(("-r", request.referer))

        params.append(url)

        return params

    def _send_probe(self, request, errors):

        probe = None
        retries = CrawlerThread._PROCESS_RETRIES
        params = self._set_probe_params(request)

        while retries:

            cmd = CommandExecutor(Shared.probe_cmd + params)
            jsn = cmd.execute(Shared.options['process_timeout'] + 2)

            if jsn is None:
                errors.append(ERROR_PROBEKILLED)
                sleep(CrawlerThread._PROCESS_RETRIES_INTERVAL)  # ... ???
                retries -= 1
                continue
            else:
                probe_array = self._load_probe_json(jsn)

            if probe_array:
                probe = Probe(probe_array, request)

                if probe.status == "ok":
                    break

                errors.append(probe.errcode)

                if probe.errcode in (ERROR_CONTENTTYPE, ERROR_PROBE_TO, ERROR_FORCE_STOP):
                    break

            sleep(CrawlerThread._PROCESS_RETRIES_INTERVAL)
            retries -= 1
        return probe

    @staticmethod
    def _load_probe_json(jsn):
        if isinstance(jsn, tuple):
            jsn = jsn[0]

        try:
            jsn1 = json.loads(jsn)
            data = json.loads(jsn1['message'])
            return data
        except ValueError:
            print "-- JSON DECODE ERROR %s" % jsn
        except Exception:
            raise

    @staticmethod
    def _is_duplicate(probe, request, threshold=6):

        if probe.hash is not None:
            for url, prev_hash in Shared.hash_bucket:
                d = Simhash(prev_hash).distance(probe.hash)
                # Debug
                # print("Distance between " + request.url + " and " + url + " is " + str(d))
                if d <= threshold:
                    print("Catched near duplicate value between :" + request.url + " and " + url +
                          " with simhash distance: " + str(d))
                    Shared.hash_bucket.append((request.url, probe.hash))
                    return True

            Shared.hash_bucket.append((request.url, probe.hash))
        return False
