import unittest

from mock import patch

from core.constants import *
from core.crawl.crawler import Crawler
from core.crawl.crawler_thread import CrawlerThread
from core.crawl.lib.shared import Shared
from core.crawl.lib.probe import Probe
from core.lib.request import Request
from core.lib.thirdparty.simhash import Simhash


class CrawlerTest(unittest.TestCase):
    @patch('core.crawl.crawler.generate_filename', return_value='my_out_file-1')
    @patch('core.crawl.crawler.Database')
    def test__get_database_rename_outfile(self, database_mock, generate_filename_mock):
        db = Crawler._get_database('my_out_file', CRAWLOUTPUT_RENAME)

        generate_filename_mock.assert_called_once_with('my_out_file', out_file_overwrite=False)
        database_mock.assert_called_once_with('my_out_file-1')
        db.initialize.assert_called_once()

    @patch('core.crawl.crawler.Database')
    @patch('core.crawl.crawler.os.path.exists', return_value=True)
    @patch('core.crawl.crawler.os.path.getsize', return_value=0)
    @patch('core.crawl.crawler.os.remove')
    def test__get_database_overwrite_outfile(
            self,
            os_remove_mock,
            os_path_getsize_mock,
            os_path_exists_mock,
            database_mock):
        db = Crawler._get_database('my_out_file', CRAWLOUTPUT_OVERWRITE)

        os_path_getsize_mock.assert_called_with('my_out_file')
        os_path_exists_mock.assert_called_with('my_out_file')
        self.assertEqual(os_path_exists_mock.call_count, 3)
        os_remove_mock.assert_called_once_with('my_out_file')
        database_mock.assert_called_once_with('my_out_file')
        db.initialize.assert_called_once()

    @patch('core.crawl.crawler.Database')
    @patch('core.crawl.crawler.os.path.exists', return_value=True)
    @patch('core.crawl.crawler.os.path.getsize', return_value=2)
    def test__get_database_complete_outfile(self, os_path_getsize_mock, os_path_exists_mock, database_mock):
        db = Crawler._get_database('my_out_file', CRAWLOUTPUT_COMPLETE)

        database_mock.assert_called_once_with('my_out_file')
        os_path_getsize_mock.assert_called_with('my_out_file')
        os_path_exists_mock.assert_called_with('my_out_file')
        self.assertEqual(os_path_exists_mock.call_count, 2)
        self.assertEqual(db.initialize.call_count, 0)

    @patch('core.crawl.crawler.Database')
    @patch('core.crawl.crawler.os.path.exists', return_value=False)
    def test__get_database_resume_new_outfile(self, os_path_exists_mock, database_mock):
        db = Crawler._get_database('my_out_file', CRAWLOUTPUT_RESUME)

        database_mock.assert_called_once_with('my_out_file')
        os_path_exists_mock.assert_called_once_with('my_out_file')
        self.assertEqual(db.initialize.call_count, 1)


class CrawlerThreadTest(unittest.TestCase):

    def test_is_not_duplicates_bucket_empty(self):
        Shared.block_duplicates = True
        Shared.hash_bucket = []
        request = Request("type1", "POST", "http://example.com", data="example data")
        probe_ar = [[u'cookies', [u'cookie']],
                    [u'html', u"""This is a completely new page, how exciting it is!"""],
                    [u'request', {u'url': u'http://example1.com', u'data': None, u'type': u'link', u'method': u'GET'}],
                    {u'status': u'ok'}]

        probe = Probe(probe_ar, request)

        self.assertFalse(CrawlerThread._is_duplicate(probe, request))

    def test_is_not_duplicate_bucket_not_empty(self):
        Shared.block_duplicates = True
        Shared.hash_bucket = []
        page0 = """You have reached Test Case for the time 1!<br><center><a href="foobar">click me</a>"""
        hash_page0 = Simhash(page0)
        Shared.hash_bucket.append(("http://example0.com", hash_page0))

        request = Request("type1", "POST", "http://example1.com", data="example data")
        probe_ar = [[u'cookies', [u'cookie']],
                    [u'html', u"""This is a completely new page, how exciting it is!"""],
                    [u'request', {u'url': u'http://example1.com', u'data': None, u'type': u'link', u'method': u'GET'}],
                    {u'status': u'ok'}]

        probe = Probe(probe_ar, request)

        self.assertFalse(CrawlerThread._is_duplicate(probe, request))

    def test_is_near_duplicate(self):
        Shared.block_duplicates = True
        Shared.hash_bucket = []
        page0 = """You have reached Test Case for the time 1!<br><center><a href="foobar">click me</a>"""
        hash_page0 = Simhash(page0)
        Shared.hash_bucket.append(("http://example0.com", hash_page0))

        request = Request("type1", "POST", "http://example1.com", data="example data")
        probe_ar = [[u'cookies', [u'cookie']],
                    [u'html',
                     u"""You have reached Test Case for the time 2!<br><center><a href="foobar">click me</a></center>"""
                     ],
                    [u'request', {u'url': u'http://example1.com', u'data': None, u'type': u'link', u'method': u'GET'}],
                    {u'status': u'ok'}]

        probe = Probe(probe_ar, request)

        self.assertTrue(CrawlerThread._is_duplicate(probe, request))

    def test_request_has_no_html_bucket_empty(self):
        Shared.block_duplicates = True
        Shared.hash_bucket = []
        request = Request("type1", "POST", "http://example.com", data="example data")
        probe_ar = [[u'cookies', [u'cookie']],
                    [u'request', {u'url': u'http://example1.com', u'data': None, u'type': u'link', u'method': u'GET'}],
                    {u'status': u'ok'}]

        probe = Probe(probe_ar, request)

        self.assertFalse(CrawlerThread._is_duplicate(probe, request))

    def test_request_has_no_html_bucket_not_empty(self):
        Shared.block_duplicates = True
        Shared.hash_bucket = []
        page0 = """You have reached Test Case for the time 1!<br><center><a href="foobar">click me</a>"""
        hash_page0 = Simhash(page0)
        Shared.hash_bucket.append(("http://example0.com", hash_page0))
        request = Request("type1", "POST", "http://example.com", data="example data")
        probe_ar = [[u'cookies', [u'cookie']],
                    [u'request',
                     {u'url': u'http://example1.com', u'data': None, u'type': u'link', u'method': u'GET'}],
                    {u'status': u'ok'}]

        probe = Probe(probe_ar, request)

        self.assertFalse(CrawlerThread._is_duplicate(probe, request))