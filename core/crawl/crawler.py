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

import getopt
import json
import os
import re
import ssl
import string
import sys
import threading
import time
import urllib2
from random import choice
from urllib import unquote
from urlparse import urlsplit

from core.constants import *
from core.crawl.crawler_thread import CrawlerThread
from core.crawl.lib.crawl_result import CrawlResult
from core.crawl.lib.shared import Shared
from core.crawl.lib.utils import request_is_crawlable, request_depth, request_post_depth, \
    adjust_requests
from core.lib.cookie import Cookie
from core.lib.database import Database
from core.lib.exception import NotHtmlException
from core.lib.http_get import HttpGet
from core.lib.request import Request
from core.lib.utils import get_program_infos, getrealdir, print_progressbar, stdoutw, \
    normalize_url, cmd_to_str, generate_filename, get_probe_cmd


# TODO: clean the exception handling (no more `except Exception:`)
# see: http://stackoverflow.com/questions/2052390/manually-raising-throwing-an-exception-in-python

# TODO: using argparse instead of custom handling of options https://docs.python.org/3.5/howto/argparse.html

# TODO: replace print() by logger.info()/logger.warning()


class Crawler:
    def __init__(self, argv):

        self.arg = argv

        self.base_dir = getrealdir(__file__)

        self.crawl_start_date = int(time.time())
        self.crawl_end_date = None

        self._defaults = CRAWLER_DEFAULTS

        # initialize crawl config
        self._start_referer = None

        self._threads = []
        self._num_threads = self._defaults['num_threads']

        self._output_mode = self._defaults['output_mode']
        self._cookie_string = None
        self._display_progress = True
        self._verbose = False
        self._initial_checks = True
        self._http_auth = None
        self._get_robots_txt = True

        # initialize probe
        self._probe = {
            "cmd": ["node"],
            "options": []
        }
        self._setup_shared()

    def _usage(self):
        print("""htcap crawler ver {version}
usage: htcap crawl [options] url outfile
Options:
  -h              this help
  -q              do not display progress information
  -v              be verbose
  -o OUTPUT_MODE  set output mode in case the given outfile already exist:
                    - {crawl_output_rename}: rename the current outfile (default)
                    - {crawl_output_overwrite}: overwrite the existing outfile
                    - {crawl_output_resume}: use the same file and last crawl data
                              it will follow any "not crawled" url from the last crawl
                              as a starting url
                    - {crawl_output_complete}: use the same file and complete the existing data set
                                it will not follow any previously found urls (only the one provided in arguments)
  -m MODE         set crawl mode:
                    - {crawl_mode_passive}: do not interact with the page
                    - {crawl_mode_active}: trigger events
                    - {crawl_mode_aggressive}: also fill input values and crawl forms (default)
  -s SCOPE        set crawl scope
                    - {crawl_scope_domain}: limit crawling to current domain (default)
                    - {crawl_scope_directory}: limit crawling to current directory (and subdirectories)
                    - {crawl_scope_url}: do not crawl, just analyze a single page
  -D DEPTH        maximum crawl depth (default: {max_depth})
  -P DEPTH        maximum crawl depth for consecutive forms (default: {max_post_depth})
  -F              even if in aggressive mode, do not crawl forms
  -d DOMAINS      comma separated list of allowed domains (ex *.target.com)
  -c COOKIES      cookies as json or name=value pairs separated by semicolon
  -C COOKIE_FILE  path to file containing COOKIES
  -r REFERRER     set initial referrer
  -x EXCLUDED     comma separated list of urls to exclude (regex) - ie logout urls
  -p PROXY        proxy string protocol:host:port - protocol can be 'http' or 'socks5'
  -n THREADS      number of parallel threads (default: {num_threads})
  -A CREDENTIALS  username and password used for HTTP authentication separated by a colon
  -U USER_AGENT   set user agent
  -t TIMEOUT      maximum seconds spent to analyze a page (default {process_timeout})
  -S              skip initial checks
  -G              group query_string parameters with the same name ('[]' ending excluded)
  -N              don't normalize URL path (keep ../../)
  -R REDIRECTS    maximum number of redirects to follow (default {max_redirects})
  -I              ignore robots.txt (otherwise it will try to read the robots.txt related to the start url provided)
  -O              don't override timeout functions (setTimeout, setInterval)
  -K              keep elements in the DOM (prevent removal)
  -e SEED         seed used to generate strings during crawl
  -b              block duplicate content and near-duplicates.
""".format(
            version=get_program_infos()['version'],
            crawl_output_rename=CRAWLOUTPUT_RENAME,
            crawl_output_overwrite=CRAWLOUTPUT_OVERWRITE,
            crawl_output_resume=CRAWLOUTPUT_RESUME,
            crawl_output_complete=CRAWLOUTPUT_COMPLETE,
            crawl_mode_passive=CRAWLMODE_PASSIVE,
            crawl_mode_active=CRAWLMODE_ACTIVE,
            crawl_mode_aggressive=CRAWLMODE_AGGRESSIVE,
            crawl_scope_domain=CRAWLSCOPE_DOMAIN,
            crawl_scope_directory=CRAWLSCOPE_DIRECTORY,
            crawl_scope_url=CRAWLSCOPE_URL,
            max_depth=Shared.options['max_depth'],
            max_post_depth=Shared.options['max_post_depth'],
            num_threads=self._defaults['num_threads'],
            process_timeout=self._defaults['process_timeout'],
            max_redirects=self._defaults['max_redirects']
        ))

    def _setup_shared(self):
        """
        instantiate crawler, probe and start the crawling loop

        :param argv:
        """
        Shared.options = self._defaults  # initialize shared options
        # initialize threads conditions
        Shared.th_condition = threading.Condition()
        Shared.main_condition = threading.Condition()

        # validate probe presence
        if not self._probe["cmd"]:
            print("Error: unable to find probe")
            sys.exit(1)

        # retrieving user arguments
        try:
            opts, args = getopt.getopt(self.arg, 'ho:qvm:s:D:P:Fd:c:C:r:x:p:n:A:U:t:SGNR:IOKe:b')
        except getopt.GetoptError as err:
            print(str(err))
            self._usage()
            sys.exit(1)

        if len(args) < 2:  # if no start url and file name
            self._usage()
            print('* Error: missing url and/or outfile')
            sys.exit(1)

        for o, v in opts:
            if o == '-h':  # help
                self._usage()
                sys.exit(0)
            elif o == '-c':  # cookie string
                self._cookie_string = v
            elif o == '-C':  # cookie file
                try:
                    with open(v) as cf:
                        self._cookie_string = cf.read()
                except Exception as e:
                    print("* Error reading cookie file: {}".format(str(e)))
                    sys.exit(1)
            elif o == '-r':  # start referrer
                self._start_referer = v
            elif o == '-n':  # number of threads
                self._num_threads = int(v)
            elif o == '-t':  # time out
                Shared.options['process_timeout'] = int(v)
            elif o == '-q':  # quiet
                self._display_progress = False
            elif o == '-A':  # authentication
                self._http_auth = v
            elif o == '-p':  # proxy
                proxy = v.split(":")
                if proxy[0] not in ("http", "socks5"):
                    print("* Error: only http and socks5 proxies are supported")
                    sys.exit(1)
                Shared.options['proxy'] = {"proto": proxy[0], "host": proxy[1], "port": proxy[2]}
            elif o == '-d':  # allowed domains
                for ad in v.split(","):
                    # convert *.domain.com to *.\.domain\.com
                    pattern = re.escape(ad).replace("\\*\\.", "((.*\\.)|)")
                    Shared.allowed_domains.add(pattern)
            elif o == '-x':  # excluded urls
                for eu in v.split(","):
                    Shared.excluded_urls.add(eu)
            elif o == "-G":
                Shared.options['group_qs'] = True
            elif o == "-o":  # output file mode
                if v not in (CRAWLOUTPUT_OVERWRITE, CRAWLOUTPUT_RENAME, CRAWLOUTPUT_RESUME, CRAWLOUTPUT_COMPLETE):
                    self._usage()
                    print("* Error: wrong output mode set '%s'\n" % v)
                    sys.exit(1)
                self._output_mode = v
            elif o == "-R":  # redirects limit
                Shared.options['max_redirects'] = int(v)
            elif o == "-U":  # user agent
                Shared.options['user_agent'] = v
            elif o == "-s":  # crawl scope
                if v not in (CRAWLSCOPE_DOMAIN, CRAWLSCOPE_DIRECTORY, CRAWLSCOPE_URL):
                    self._usage()
                    print("* ERROR: wrong scope set '%s'" % v)
                    sys.exit(1)
                Shared.options['scope'] = v
            elif o == "-m":  # crawl mode
                if v not in (CRAWLMODE_PASSIVE, CRAWLMODE_ACTIVE, CRAWLMODE_AGGRESSIVE):
                    self._usage()
                    print("* ERROR: wrong mode set '%s'" % v)
                    sys.exit(1)
                Shared.options['mode'] = v
            elif o == "-S":  # skip initial checks
                self._initial_checks = False
            elif o == "-I":  # ignore robots.txt
                self._get_robots_txt = False
            elif o == "-D":  # crawling depth
                Shared.options['max_depth'] = int(v)
            elif o == "-P":  # crawling depth for forms
                Shared.options['max_post_depth'] = int(v)
            elif o == "-O":  # do not override javascript timeout
                Shared.options['override_timeout_functions'] = False
            elif o == "-F":  # do not crawl forms
                Shared.options['crawl_forms'] = False
            elif o == "-v":  # verbose
                self._verbose = True
            elif o == "-e":  # seed for random value
                Shared.options["random_seed"] = v
            elif o == "-b":  # block duplicates and near-duplicates
                Shared.block_duplicates = True

        # warn about -d option in domain scope mode
        if Shared.options['scope'] != CRAWLSCOPE_DOMAIN and len(Shared.allowed_domains) > 0:
            print("* Warning: option -d is valid only if scope is %s" % CRAWLSCOPE_DOMAIN)

        # initialize cookies
        if self._cookie_string:
            try:

                start_cookies = self._parse_cookie_string(self._cookie_string)
                for cookie in start_cookies:
                    Shared.start_cookies.append(Cookie(cookie, Shared.start_url))

            except Exception as e:
                print("error decoding cookie string: {}".format(str(e)))
                sys.exit(1)

        # retrieve start url and output file arguments
        Shared.start_url = normalize_url(args[0])
        self._outfile_name = args[1]

        # add start url domain to allowed domains
        purl = urlsplit(Shared.start_url)
        Shared.allowed_domains.add(purl.hostname)

        # warn about ssl context in python 2
        if not hasattr(ssl, "SSLContext"):
            print(
                "* WARNING: SSLContext is not supported with this version of python,"
                " consider to upgrade to >= 2.7.9 in case of SSL errors")

    def run(self):
        def _is_not_in_past_requests(request):
            """
            check if the given request is present in Shared.requests or start_requests
            """
            is_in_request = True
            for r in Shared.requests + start_requests:
                if r == request:
                    is_in_request = False
            return is_in_request

        # get database
        try:
            database = self._get_database(self._outfile_name, self._output_mode)

            crawl_id = database.save_crawl_info(
                htcap_version=get_program_infos()['version'],
                target=Shared.start_url,
                start_date=self.crawl_start_date,
                commandline=cmd_to_str(self.arg),
                user_agent=Shared.options['user_agent'],
                start_cookies=Shared.start_cookies
            )

            # if the current crawl is not the first one
            if crawl_id > 1:

                # retrieving options from the last crawl
                random_seed, cookies = database.retrieve_crawl_info(crawl_id - 1)

                # if the db had a seed and none were provided before
                if random_seed and not Shared.options.get("random_seed"):
                    Shared.options["random_seed"] = random_seed

                # if no cookie was provided and some exist from the last crawl
                if len(Shared.start_cookies) <= 0 and cookies != "[]" and cookies is not None:
                    for cookie_string in self._parse_cookie_string(cookies):
                        Shared.start_cookies.append(Cookie(cookie_string))

            # if no seed have been set yet
            if not Shared.options.get("random_seed"):
                Shared.options["random_seed"] = self._generate_random_string(20)

        except Exception as e:
            print(str(e))
            sys.exit(1)

        # set probe arguments
        self._set_probe()

        Shared.probe_cmd = self._probe["cmd"] + self._probe["options"]

        start_requests = []

        # create the start request object from provided arguments
        start_request_from_args = Request(
            REQTYPE_LINK, "GET", Shared.start_url, set_cookie=Shared.start_cookies,
            http_auth=self._http_auth, referer=self._start_referer)

        # check starting url
        if self._initial_checks:
            try:
                self._check_request(start_request_from_args)
                stdoutw(". ")
            except KeyboardInterrupt:
                print("\nAborted")
                sys.exit(0)

        if self._output_mode in (CRAWLOUTPUT_RESUME, CRAWLOUTPUT_COMPLETE):
            try:
                # make the start url given in arguments crawlable again
                database.connect()
                database.save_request(start_request_from_args)
                database.make_request_crawlable(start_request_from_args)
                database.commit()
                database.close()

                # feeding the "done" request list from the db
                Shared.requests.extend(database.get_crawled_request())
                Shared.requests_index = len(Shared.requests)

                # if resume, add requests from db
                if self._output_mode == CRAWLOUTPUT_RESUME:
                    start_requests.extend(database.get_not_crawled_request())

                # if request from args is neither in past or future requests
                if _is_not_in_past_requests(start_request_from_args):
                    start_requests.append(start_request_from_args)
            except Exception as e:
                print(str(e))
                sys.exit(1)
        else:
            start_requests.append(start_request_from_args)

        # retrieving robots.txt content
        if self._get_robots_txt:
            try:
                start_requests.extend(
                    filter(_is_not_in_past_requests, self._get_requests_from_robots(start_request_from_args))
                )
            except KeyboardInterrupt:
                print("\nAborted")
                sys.exit(0)

        # save starting request to db
        database.connect()
        database.begin()
        for req in start_requests:
            database.save_request(req)
        database.commit()
        database.close()

        print(
            "\nDone: {} starting url(s) and {} url(s) already crawled".format(len(start_requests), len(Shared.requests))
        )

        # starting crawling threads
        print("Database %s initialized, crawl starting with %d threads" % (database, self._num_threads))

        for n in range(0, self._num_threads):
            thread = CrawlerThread()
            self._threads.append(thread)
            thread.start()

        # running crawl loop
        self._main_loop(self._threads, start_requests, database, self._display_progress, self._verbose)

        self._kill_threads(self._threads)

        self.crawl_end_date = int(time.time())

        print("Crawl finished, %d pages analyzed in %d minutes" % (
            Shared.requests_index, (self.crawl_end_date - self.crawl_start_date) / 60))

        # update end date in db
        database.update_crawl_info(crawl_id, self.crawl_end_date, Shared.options["random_seed"], Shared.end_cookies)


    def _main_loop(self, threads, start_requests, database, display_progress=True, verbose=False):
        pending = len(start_requests)
        crawled = 0
        req_to_crawl = start_requests
        try:
            while True:

                if display_progress and not verbose:
                    tot = (crawled + pending)
                    print_progressbar(tot, crawled, self.crawl_start_date, "pages processed")

                if pending == 0:
                    # is the check of running threads really needed?
                    running_threads = [t for t in threads if t.status == THSTAT_RUNNING]
                    if len(running_threads) == 0:
                        if display_progress or verbose:
                            print("")
                        break

                if len(req_to_crawl) > 0:
                    Shared.th_condition.acquire()
                    Shared.requests.extend(req_to_crawl)
                    Shared.th_condition.notifyAll()
                    Shared.th_condition.release()

                req_to_crawl = []
                Shared.main_condition.acquire()
                Shared.main_condition.wait(1)
                if len(Shared.crawl_results) > 0:
                    database.connect()
                    database.begin()
                    for result in Shared.crawl_results:
                        crawled += 1
                        pending -= 1
                        if verbose:
                            print("crawl result for: %s " % result.request)
                            if len(result.request.user_output) > 0:
                                print("  user: %s" % json.dumps(result.request.user_output))
                            if result.errors:
                                print("* crawler errors: %s" % ", ".join(result.errors))

                        database.save_crawl_result(result, True)
                        for req in result.found_requests:

                            if verbose:
                                print("  new request found %s" % req)

                            database.save_request(req)
                            if request_is_crawlable(req) and req not in Shared.requests and req not in req_to_crawl:
                                if request_depth(req) > Shared.options['max_depth'] or request_post_depth(req) > \
                                        Shared.options['max_post_depth']:
                                    if verbose:
                                        print("  * cannot crawl: %s : crawl depth limit reached" % req)
                                    result = CrawlResult(req, errors=[ERROR_CRAWLDEPTH])
                                    database.save_crawl_result(result, False)
                                    continue

                                if req.redirects > Shared.options['max_redirects']:
                                    if verbose:
                                        print("  * cannot crawl: %s : too many redirects" % req)
                                    result = CrawlResult(req, errors=[ERROR_MAXREDIRECTS])
                                    database.save_crawl_result(result, False)
                                    continue

                                pending += 1
                                req_to_crawl.append(req)

                    Shared.crawl_results = []
                    database.commit()
                    database.close()
                Shared.main_condition.release()

        except KeyboardInterrupt:
            print("\nTerminated by user")
            try:
                Shared.main_condition.release()
                Shared.th_condition.release()
            except Exception as e:
                print(str(e))
                pass

    def _set_probe(self):
        """
        set command arguments for the javascript probe
        """

        self._probe["options"].extend(("-R", Shared.options['random_seed']))

        # set probe option according to choosing crawl mode
        if Shared.options['mode'] != CRAWLMODE_AGGRESSIVE:
            self._probe["options"].append("-f")  # don't fill values
        if Shared.options['mode'] == CRAWLMODE_PASSIVE:
            self._probe["options"].append("-t")  # don't trigger events

        # set probe proxy
        if Shared.options['proxy']:
            self._probe["options"].append("--proxy=%s://%s:%s" % (Shared.options['proxy']['proto'],
                                                                  Shared.options['proxy']['host'],
                                                                  Shared.options['proxy']['port']))

        # finally, set the probe script
        self._probe["cmd"].append(self.base_dir + 'probe/index.js')

        if len(Shared.excluded_urls) > 0:
            self._probe["options"].extend(("-X", ",".join(Shared.excluded_urls)))

        # self._probe["options"].extend(("-x", str(Shared.options['process_timeout'])))
        self._probe["options"].extend(("-A", Shared.options['user_agent']))

        if not Shared.options['override_timeout_functions']:
            self._probe["options"].append("-O")

    @staticmethod
    def _kill_threads(threads):
        for th in threads:
            if th.isAlive():
                th.exit = True
        # start notify() chain
        Shared.th_condition.acquire()
        Shared.th_condition.notifyAll()
        Shared.th_condition.release()

    @staticmethod
    def _parse_cookie_string(cookie_string):

        cookies = []
        try:
            cookies = json.loads(cookie_string)
        except ValueError:
            tok = re.split("; *", cookie_string)
            for t in tok:
                k, v = t.split("=", 1)
                cookies.append({"name": k.strip(), "value": unquote(v.strip())})
        except:
            raise

        return cookies

    @staticmethod
    def _check_request(request):
        """
        check if the given request resolve and return proper html file
        :param request:
        :return:
        """
        h = HttpGet(request, Shared.options['process_timeout'], 2, Shared.options['user_agent'],
                    Shared.options['proxy'])
        try:
            h.get_requests()
        except NotHtmlException:
            print("\nError: Document is not html")
            sys.exit(1)
        except Exception as e:
            print("\nError: unable to open url: %s" % e)
            sys.exit(1)

    @staticmethod
    def _get_requests_from_robots(start_request):
        """
        read robots.txt file (if any) and create a list of request based on it's content

        :return: list of request
        """
        purl = urlsplit(start_request.url)
        url = "%s://%s/robots.txt" % (purl.scheme, purl.netloc)

        getreq = Request(REQTYPE_LINK, "GET", url)
        try:
            # request, timeout, retries=None, user_agent=None, proxy=None):
            httpget = HttpGet(getreq, 10, 1, "Googlebot", Shared.options['proxy'])
            lines = httpget.get_file().split("\n")
        except urllib2.HTTPError:
            return []
        except:
            raise

        requests = []
        for line in lines:
            directive = ""
            url = None
            try:
                directive, url = re.sub("\#.*", "", line).split(":", 1)
            except Exception as e:
                print(str(e))
                continue  # ignore errors

            if re.match("(dis)?allow", directive.strip(), re.I):
                req = Request(REQTYPE_LINK, "GET", url.strip(), parent=start_request)
                if request_is_crawlable(req):
                    requests.append(req)

        return adjust_requests(requests) if requests else []

    @staticmethod
    def _generate_random_string(length):
        all_chars = string.digits + string.letters + string.punctuation
        random_string = ''.join(choice(all_chars) for _ in range(length))
        return random_string

    @staticmethod
    def _get_database(outfile_name, output_mode):
        """
        return either an existing database or a new one depending of the given output mode
        :param outfile_name:
        :param output_mode:
        :return:
        """
        file_name = outfile_name
        if output_mode == CRAWLOUTPUT_RENAME:
            file_name = generate_filename(outfile_name, out_file_overwrite=False)

        elif output_mode == CRAWLOUTPUT_OVERWRITE and os.path.exists(file_name):
            os.remove(file_name)

        database = Database(file_name)

        if not os.path.exists(file_name) or (os.path.exists(file_name) and os.path.getsize(file_name) <= 0):
            database.initialize()

        return database
