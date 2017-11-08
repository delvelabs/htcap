(function() {
    'use strict';

    const EventEmitter = require('events');
    //DEBUG:
    const logger = require('../logger');

    const probe = require('./probe');

    /**
     *
     * @param {Puppeteer} puppeteer
     * @param {String} proxy - in format: `hostname:port`
     * @return {Promise.<TResult>|*}
     */
    exports.getBrowserAndPage = function(puppeteer, proxy) {
        let browserArgs = [
            '--ignore-certificate-errors',
            '--ssl-version-max=tls1.3',
            '--ssl-version-min=tls1',
            '--disable-web-security',
            '--allow-running-insecure-content',
        ];

        if (proxy) {
            browserArgs.push(`--proxy-server=${proxy}`);
        }

        return puppeteer.launch({
            headless: true,
            ignoreHTTPSErrors: true,
            args: browserArgs,
        })
            .then(createdBrowser => {
                return createdBrowser.newPage()
                    .then(createdPage => {
                        return [createdBrowser, createdPage];
                    });
            });
    };

    class Handler extends EventEmitter {
        /**
         * @constructor
         */
        constructor(page, constants, options) {
            super();
            this._page = page;
            this._constants = constants;
            this._options = options;
            this._lastRedirectResponse = undefined;
            this._reformatFirstRequest = (options.referer || options.sendPOST);
        }

        initialize() {
            this._page.on('request', interceptedRequest => {
                //DEBUG:
                // logger.debug(`intercepted request: ${interceptedRequest.resourceType} ${interceptedRequest.url}`);

                // block image loading
                if (interceptedRequest.resourceType === 'image') {
                    interceptedRequest.abort();

                    // Block redirect
                    // Since no option exist in puppeteer, this is the workaround proposed here:
                    // https://github.com/GoogleChrome/puppeteer/issues/1132#issuecomment-339420642
                } else if (this._lastRedirectResponse && this._lastRedirectResponse.headers.location === interceptedRequest.url) {
                    this.getCookies()
                        .then(cookies => {

                            let cookiesResult = ['cookies', cookies],
                                status = {'status': 'ok', 'redirect': interceptedRequest.url};
                            this.emit(Handler.Events.ProbeResult, cookiesResult);
                            this.emit(Handler.Events.Finished, 0, status);

                            interceptedRequest.abort();
                        });
                    // Set the first request as POST or/and Headers
                } else if (this._reformatFirstRequest) {

                    let overrides = {headers: interceptedRequest.headers};

                    if (this._options.sendPOST) {
                        overrides.method = 'POST';
                        overrides.postData = this._options.POSTData || undefined;
                    }

                    if (this._options.referer) {
                        overrides.headers['Referer'] = this._options.referer;
                    }

                    interceptedRequest.continue(overrides)
                        .then(() => {
                            this._reformatFirstRequest = false;
                        });

                } else {
                    interceptedRequest.continue();
                }

            });

            this._page.on('response', response => {
                if (_isRedirect(response)) {
                    this._lastRedirectResponse = response;
                }
            });

            this._page.on('framenavigated', frameTo => {
                //DEBUG:
                // logger.debug(`framenavigated to ${frameTo.url()}`);

                this._page.evaluate((url) => {
                    window.__PROBE__.printLink(url);
                }, frameTo.url());
            });

            this._page.on('dialog', dialog => {
                //DEBUG:
                // logger.debug(`Page dialog, type "${dialog.type}": "${dialog.message()}"`);
                dialog.accept();
            });

            this._page.on('error', error => {
                //DEBUG:
                logger.debug(`Page crash: "${error.code}", "${error.message()}"`);
                let status = {'status': 'error', 'code': 'pageCrash', 'message': `Page crash with: "${error.code}", "${error.message()}"`};
                this.emit(Handler.Events.Finished, 1, status);
            });

            // //DEBUG:
            this._page.on('console', consoleMessage => {
                logger.debug(`Page console message, type "${consoleMessage.type}": "${consoleMessage.text}"`);
            });
            // //DEBUG:
            // this._page.on('frameattached', frameTo => {
            //     logger.debug(`frameattached to ${frameTo.url()}`);
            // });
            // //DEBUG:
            // this._page.on('requestfailed', failedRequest => {
            //     logger.debug(`requestfailed: ${failedRequest.url}`);
            // });
            // //DEBUG:
            // this._page.on('requestfinished', finishedRequest => {
            //     logger.debug(`requestfinished: ${finishedRequest.response().status}, ${finishedRequest.method} ${finishedRequest.url}`);
            // });
            // //DEBUG:
            // this._page.on('load', () => {
            //     logger.debug('load done');
            // });


            // set function to return value from probe
            this._page.exposeFunction('__PROBE_FN_RETURN_REQUEST__', (request) => {
                this.emit(Handler.Events.ProbeResult, request);
            });

            // set function to request end from probe
            this._page.exposeFunction('__PROBE_FN_REQUEST_END__', () => {
                //DEBUG:
                logger.debug('Probe finished');
                let status = {'status': 'ok'};
                this.emit(Handler.Events.Finished, 0, status);
            });

            return Promise.all([
                this._page.setUserAgent(this._options.userAgent),
                this._page.setCookie(...this._options.cookies),
                this._page.setViewport(this._constants.viewport),
                this._page.setRequestInterceptionEnabled(true),
                this._page.authenticate(this._options.httpAuth),
            ])
                .then(() => {
                    this._setProbe();
                    return this._page;
                });
        }

        _setProbe() {
            // on every new document, initializing the probe into the page context
            this._page.evaluateOnNewDocument(probe.setProbe, ...[this._options, this._constants]);
        }

        startProbe() {
            this._page.evaluate(() => {
                window.__PROBE__.startAnalysis();
            });
        }

        /**
         * @return {Promise|Cookie}
         */
        getCookies() {
            return this._page.cookies();
        }
      }

    Handler.Events = {
        Finished: 'finished',
        ProbeResult: 'probeResult',
    };

    function _isRedirect(response) {
        return [301, 302, 303, 307, 308].includes(response.status) && response.request().resourceType === 'document';
    }

    exports.Handler = Handler;

})();
