/**
 * @todo (blocked):
 * - make possible to send custom headers (set a referer) on page.goto() see: {@link https://github.com/GoogleChrome/puppeteer/issues/1062}
 *     and {@link https://github.com/GoogleChrome/puppeteer/issues/686}
 * - block navigation away and return content related to "navigationRequest" as in PhantomJS see: {@link https://github.com/GoogleChrome/puppeteer/issues/823}
 *   possible workaround: watching `onunload` page event to prevent navigation
 *
 * @todo (nice to have):
 * - add a debug level
 * - store headers for every request (mainly cookies and referrer) to enable a better "replay"
 * - also analyse the error pages (40x and 50x)
 */

(function() {
    'use strict';

    const process = require('process');

    const logger = require('./logger');
    const puppeteer = require('puppeteer');

    const constants = require('./src/constants').constants;
    const utils = require('./src/utils');

    const pageHandler = require('./src/page-handler');

    let options = utils.getOptionsFromArgs(),
        result = [],
        browser,
        handler;

    let startTime = new Date();

    // handling SIGTERM signal
    process.on('SIGTERM', () => {
        result.push({'status': 'error', 'code': 'interruptReceived'});
        _requestJobEnd();
    });

    function _requestJobEnd(exitCode) {

        //DEBUG:
        logger.debug('closing Node process');

        logger.log('debug', `${result.length} results in ${(Date.now() - startTime) / 1000} sec : ${JSON.stringify(result)}`);
        logger.log('info', `${JSON.stringify(result)}`);

        if (browser) {
            browser.close()
                .then(() => {
                    process.exit(exitCode);
                });
        } else {
            process.exit(exitCode);
        }
    }

    function run([newBrowser, newPage]) {

        browser = newBrowser;

        handler = new pageHandler.Handler(newPage, constants, options);

        handler.on('finished', (exitCode, status) => {
            result.push(status);
            _requestJobEnd(exitCode);
        });

        handler.on('probeResult', (newResult) => {
            result.push(newResult);
        });

        handler.initialize()
            .then((page) => {
                page.goto(options.startUrl.href, {waitUntil: 'networkidle'})
                    .then(response => {

                        if (response.ok) {
                            // checking if it's some HTML document
                            if (response.headers['content-type']
                                        .toLowerCase()
                                        .includes('text/html')) {

                                handler.getCookies()
                                        .then(cookies => {
                                            result.push(['cookies', cookies]);
                                        });
                                page.content()
                                        .then(html => {
                                          result.push(['html', html]);
                                        });
                                // DEBUG:
                                logger.debug('starting the probe');
                                // start analysis on the page
                                handler.startProbe();
                            } else {
                                result.push({'status': 'error', 'code': 'contentType', 'message': `content type is ${response.headers['content-type']}`});
                                _requestJobEnd();
                            }
                        } else {
                            result.push({'status': 'error', 'code': 'load', 'message': `response code is ${response.status}`});
                            _requestJobEnd(1);
                        }
                    },
                    (error) => {
                        // logger.error(error);
                        result.push({'status': 'error', 'code': 'load', 'message': `error is ${error}`});
                        _requestJobEnd(1);
                    });
            }, (error) => {
                // logger.error(error);
                result.push({'status': 'error', 'code': 'probeError', 'message': `error is ${error}`});
                _requestJobEnd(1);
            });
    }

    pageHandler.getBrowserAndPage(puppeteer, options.proxyAddress)
        .then(run);

})();
