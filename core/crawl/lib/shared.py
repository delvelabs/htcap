# -*- coding: utf-8 -*- 

"""
HTCAP - beta 1
Author: filippo.cavallarin@wearesegment.com

This program is free software; you can redistribute it and/or modify it under 
the terms of the GNU General Public License as published by the Free Software 
Foundation; either version 2 of the License, or (at your option) any later 
version.
"""


# TODO: make sure that only shared data are stored in this object

class Shared:
    """
    data shared between threads
    """

    def __init__(self):
        pass

    main_condition = None
    th_condition = None
    block_duplicates = False
    requests = []
    requests_index = 0
    crawl_results = []

    start_url = ""
    start_cookies = []
    end_cookies = []
    allowed_domains = set()
    excluded_urls = set()

    probe_cmd = []

    options = {}

    hash_bucket = []
