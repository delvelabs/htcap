# -*- coding: utf-8 -*- 

"""
HTCAP - beta 1
Author: filippo.cavallarin@wearesegment.com

This program is free software; you can redistribute it and/or modify it under 
the terms of the GNU General Public License as published by the Free Software 
Foundation; either version 2 of the License, or (at your option) any later 
version.
"""

from core.constants import *
from core.lib.cookie import Cookie
from core.lib.request import Request


class Probe:
    def __init__(self, data, parent):
        self.status = "ok"
        self.requests = []
        self.cookies = []
        self.redirect = None
        # if True the probe returned no error BUT the json is not closed properly
        self.partialcontent = False
        self.user_output = []

        status = data.pop()

        if status['status'] == "error":
            self.status = "error"
            self.errcode = status['code']

        if "partialcontent" in status:
            self.partialcontent = status['partialcontent']

        # grap cookies before creating rquests
        for key, val in data:
            if key == "cookies":
                for cookie in val:
                    self.cookies.append(Cookie(cookie, parent.url))

        if "redirect" in status:
            self.redirect = status['redirect']
            r = Request(REQTYPE_REDIRECT, "GET", self.redirect, parent=parent, set_cookie=self.cookies,
                        parent_db_id=parent.db_id)
            self.requests.append(r)

        for key, val in data:
            if key == "request":
                trigger = val['trigger'] if 'trigger' in val else None
                r = Request(val['type'], val['method'], val['url'], parent=parent, set_cookie=self.cookies,
                            data=val['data'], trigger=trigger, parent_db_id=parent.db_id)
                self.requests.append(r)
            elif key == "user":
                self.user_output.append(val)



            # @TODO handle cookies set by ajax (in probe too)
