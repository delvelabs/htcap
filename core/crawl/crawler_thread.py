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
import threading
import uuid
from tempfile import NamedTemporaryFile
from time import sleep

from core.constants import *
from core.crawl.lib.crawl_result import CrawlResult
from core.crawl.lib.probe import Probe
from core.crawl.lib.shared import Shared
from core.crawl.lib.utils import adjust_requests
from core.lib.exception import ThreadExitRequestException
from core.lib.http_get import HttpGet
from core.lib.shell import CommandExecutor


class CrawlerThread(threading.Thread):
    _PROCESS_RETRIES_INTERVAL = 0.5
    _PROCESS_RETRIES = 2

    def __init__(self):
        threading.Thread.__init__(self)

        self.status = THSTAT_RUNNING
        self.exit = False

        self._thread_uuid = uuid.uuid4()
        self._cookie_file = NamedTemporaryFile(prefix="htcap_cookie_file-", suffix=".json")
        print(self._cookie_file.name)
        # self._result_file = NamedTemporaryFile(prefix="htcap_result_file-", suffix=".json")

    def run(self):
        self._crawl()

    def _crawl(self):

        while True:
            requests = []
            errors = []

            try:
                request = self._wait_request()
            except ThreadExitRequestException:
                self._cookie_file.close()
                # self._result_file.close()
                return
            except Exception as e:
                print("-->" + str(e))
                continue

            probe = self._send_probe(request, errors)

            if probe:
                if probe.status == "ok" or probe.errcode == ERROR_PROBE_TO:

                    requests = probe.requests

                    if probe.html:
                        request.html = probe.html

                    if len(probe.user_output) > 0:
                        request.user_output = probe.user_output

                    # if the probe return some cookies set it has the last one
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

    @staticmethod
    def _load_probe_json(jsn):
        jsn = jsn.strip()
        if not jsn:
            jsn = "["
        if jsn[-1] != "]":
            jsn += '{"status":"ok", "partialcontent":true}]'
        try:
            return json.loads(jsn)
        except Exception as e:
            print ("-- %s | %s" % (e, jsn))
            raise

    def _send_probe(self, request, errors):

        url = request.url
        probe = None
        retries = CrawlerThread._PROCESS_RETRIES
        params = []
        cookies = []

        if request.method == "POST":
            params.append("-P")
            if request.data:
                params.extend(("-D", request.data))

        if len(request.cookies) > 0:
            for cookie in request.cookies:
                cookies.append(cookie.get_dict())
            print("cookies: {} | {}".format(len(cookies), json.dumps(cookies)))
            self._cookie_file.truncate(0)
            self._cookie_file.write(json.dumps(cookies))
            self._cookie_file.flush()
            os.fsync(self._cookie_file.fileno())
            print(os.path.getsize(self._cookie_file.name))

            params.extend(("-c", self._cookie_file.name))

        if request.http_auth:
            params.extend(("-p", request.http_auth))

        if Shared.options['set_referer'] and request.referer:
            params.extend(("-r", request.referer))

        params.extend(("-i", str(request.db_id)))

        params.append(url)
        # params.append(self._result_file.name)

        while retries:
            print("cmd: {}".format(json.dumps(params)))
            cmd = CommandExecutor(Shared.probe_cmd + params)
            jsn = cmd.execute(Shared.options['process_timeout'] + 2)

            if cmd.err:
                print('Error: {}'.format(cmd.err))

            if jsn is None:
                errors.append(ERROR_PROBEKILLED)
                sleep(CrawlerThread._PROCESS_RETRIES_INTERVAL)  # ... ???
                retries -= 1
                continue

            # try to decode json also after an exception .. sometimes phantom crashes BUT returns a valid json ..
            if jsn and type(jsn) is not str:
                jsn = jsn[0]

            # print("result_file: {} | {}".format(self._thread_uuid, self._result_file.read()))
            probe_array = self._load_probe_json(jsn)

            if probe_array:
                probe = Probe(probe_array, request)

                if probe.status == "ok":
                    break

                errors.append(probe.errcode)

                if probe.errcode in (ERROR_CONTENTTYPE, ERROR_PROBE_TO):
                    break

            sleep(CrawlerThread._PROCESS_RETRIES_INTERVAL)
            retries -= 1

        return probe
