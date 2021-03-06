/*eslint no-console: off */
(function() {
    'use strict';

    exports.setProbe = function setProbe(options, constants) {

        /**
         * Class EventLoop Manager
         * Responsibility:
         * Managing the eventLoop to ensure that every code execution (from the page or from the probe)
         * is completely done before launching anything else.
         * Since the possible actions on the page are _user triggered_, the executed code is design to be triggered by
         * a _normal_ interaction through standard HID, not automated. So it is important to give time
         * to the JS stack to empty before launching anything new.
         *
         * Possible actions to be schedule are: DOM Assessment and event triggering.
         *
         * Upon schedule, the action will take place as soon as the eventLoop is empty and nothing is waiting
         * to be completed (like an XHR request).
         *
         * The logic is (in this order):
         * if a XHR has been sent or is done, do nothing (ie. wait the next event loop before acting)
         * then if a DOM Assessment is waiting, do it first.
         * then if a event is waiting to be triggered, trigger it.
         *
         * A new DOM Assessment is schedule every time the DOM is modified.
         * A new event is schedule for every triggerable event on every element in the DOM.
         *
         *
         * more info on {@link https://developer.mozilla.org/en-US/docs/Web/JavaScript/EventLoop MDN}
         */
        class EventLoopManager {

            /**
             * @param probe the probe from where it's initialized
             * @param config - configurations of the manager
             * @constructor
             */
            constructor(probe, config) {
                this._config = config;
                this._probe = probe;
                this._DOMAssessmentQueue = [];
                this._toBeTriggeredEventsQueue = [];
                this._sentXHRQueue = [];
                this._doneXHRQueue = [];
                this._emptyLoopCounter = 0;
                this._isReadyToClose = false;
            }


            /**
             * callback for the eventMessage listener
             * it will wait until x empty eventLoop before requesting a `doNextAction()`,
             * x being the buffer size set in constants.js
             *
             * @param eventMessage - the eventMessage triggered
             */
            eventMessageHandler(eventMessage) {

                // if it's our eventMessage
                if (eventMessage.source === window && eventMessage.data.from === this._config.messageEvent.from) {
                    eventMessage.stopPropagation();

                    if (eventMessage.data.name === this._config.messageEvent.name) {

                        // waiting x number eventLoop before doing anything (x being the buffer size)
                        if (this._emptyLoopCounter < this._config.bufferCycleSize) {
                            window.postMessage(this._config.messageEvent, '*');
                            this._emptyLoopCounter += 1;
                        } else {
                            this._emptyLoopCounter = 0;
                            this._doNextAction();
                        }
                    } else if (eventMessage.data.name === 'navigation-blocked') { // for message from the navigation blocker extension

                        console.log('received an url from chrome extension: ' + eventMessage.data.url);
                        this._probe.printLink(eventMessage.data.url);
                    }
                }
            }

            /**
             * start the eventLoopManager
             */
            start() {
                console.info('eventLoop start');
                window.postMessage(this._config.messageEvent, '*');
            }

            /**
             * Do the next action based on the priority:
             * if a XHR has been sent or is done, do nothing (ie. wait the next event loop before acting)
             * then if a DOM Assessment is waiting, do it first.
             * then if a event is waiting to be triggered, trigger it.
             * then close the manager
             */
            _doNextAction() {

                if (this._sentXHRQueue.length <= 0) { // avoiding noise
                    console.log('eventLoop doNextAction - done:', this._doneXHRQueue.length,
                        ', DOM:', this._DOMAssessmentQueue.length,
                        ', event:', this._toBeTriggeredEventsQueue.length);
                }

                if (this._sentXHRQueue.length > 0) { // if there is XHR waiting to be resolved
                    // there is still result so it is not ready to close
                    this._isReadyToClose = false;

                    // releasing the eventLoop waiting for resolution
                    window.postMessage(this._config.messageEvent, '*');

                } else if (this._doneXHRQueue.length > 0) { // if there is XHR done
                    // there is still things to do so it is not ready to close
                    this._isReadyToClose = false;

                    this._doneXHRQueue.shift();

                    window.__originalSetTimeout(function() {
                        window.postMessage(this._config.messageEvent, '*');
                    }.bind(this), this._config.afterDoneXHRTimeout);

                } else if (this._DOMAssessmentQueue.length > 0) { // if there is DOMAssessment waiting
                    // there is still things to do so it is not ready to close
                    this._isReadyToClose = false;

                    let element = this._DOMAssessmentQueue.shift();

                    console.debug('eventLoop analyzeDOM: ' + _elementToString(element));

                    // starting analyze on the next element
                    this._probe._analyzeDOMElement(element);
                    window.postMessage(this._config.messageEvent, '*');

                } else if (this._toBeTriggeredEventsQueue.length > 0) { // if there is event waiting
                    // there is still things to do so it is not ready to close
                    this._isReadyToClose = false;

                    // retrieving the next pageEvent
                    let pageEvent = this._toBeTriggeredEventsQueue.pop();

                    // setting the current element
                    this._probe._currentPageEvent = pageEvent;

                    console.debug('eventLoop pageEvent.trigger', pageEvent.element.tagName, pageEvent.eventName);

                    // Triggering the event
                    pageEvent.trigger();

                    window.__originalSetTimeout(function() {
                        window.postMessage(this._config.messageEvent, '*');
                    }.bind(this), this._config.afterEventTriggeredTimeout);
                } else if (this._isReadyToClose) {
                    console.info('eventLoop end');
                    window.__PROBE_FN_REQUEST_END__();
                } else {
                    console.log(`waiting ${this._config.beforeClosingEventLoopManagerTimeout} ms before ending the eventLoop…`);
                    window.__originalSetTimeout(function() {
                        this._isReadyToClose = true;
                        window.postMessage(this._config.messageEvent, '*');
                    }.bind(this), this._config.beforeClosingEventLoopManagerTimeout);
                }
            }

            scheduleDOMAssessment(element) {
                if (this._DOMAssessmentQueue.indexOf(element) < 0) {
                    this._DOMAssessmentQueue.push(element);
                }
            }

            nodeMutated(mutations) {
                console.log('eventLoop nodesMutated:', mutations.length);
                mutations.forEach(function(mutationRecord) {
                    if (mutationRecord.type === 'childList') {
                        for (let i = 0; i < mutationRecord.addedNodes.length; i++) {
                            let addedNode = mutationRecord.addedNodes[i];

                            console.debug('Node added:', _elementToString(mutationRecord.addedNodes[i]), mutationRecord.addedNodes[i]);

                            // see: https://developer.mozilla.org/en-US/docs/Web/API/Node/nodeType#Constants
                            if (addedNode.nodeType === Node.ELEMENT_NODE) {

                                console.debug('Node scheduled:', addedNode);
                                this.scheduleDOMAssessment(addedNode);
                            }
                        }
                    } else if (mutationRecord.type === 'attributes') {
                        let element = mutationRecord.target;

                        console.debug('eventLoop nodeMutated: attributes', _elementToString(element), mutationRecord.attributeName);

                        // removing any previous trace of triggered event on this element
                        this._probe._triggeredPageEvents.forEach(function(pageEvent, index) {
                            if (pageEvent.element === element) {
                                this._probe._triggeredPageEvents.splice(index, 1);
                            }
                        }.bind(this));
                        this.scheduleDOMAssessment(element);
                    }
                }.bind(this));
            }

            scheduleEventTriggering(pageEvent) {
                if (this._toBeTriggeredEventsQueue.indexOf(pageEvent) < 0) {

                    console.debug(`eventLoop scheduleEventTriggering: "${pageEvent.element.outerHTML}"  ${pageEvent.eventName}`);
                    this._toBeTriggeredEventsQueue.push(pageEvent);
                }
            }

            sentXHR(request) {
                if (this._sentXHRQueue.indexOf(request) < 0) {

                    console.log('eventLoop sentXHR');
                    this._sentXHRQueue.push(request);
                }
            }

            doneXHR(request) {
                if (this._doneXHRQueue.indexOf(request) < 0) {

                    console.log('eventLoop doneXHR');

                    // if the request is in the sentXHR queue
                    let i = this._sentXHRQueue.indexOf(request);
                    if (i >= 0) {
                        this._sentXHRQueue.splice(i, 1);
                    }

                    this._doneXHRQueue.push(request);
                }
            }
        }

        /**
         * Class Request
         */
        class Request {
            /**
             *  @param {String}  type
             * @param {String} method
             * @param {String} urlString
             * @param {Object=} data
             * @param {PageEvent=} triggerer - the PageEvent triggered to generate the request
             * @constructor
             */
            constructor(type, method, urlString, data, triggerer) {
                this.type = type;
                this.method = method;
                this.data = data || null;

                /** @type {PageEvent} */
                this.triggerer = triggerer;

                let u = _getAbsoluteUrl(urlString, true);
                this.url = u.href;

            }

            /**
             *  returns a unique string representation of the request. used for comparision.
             */
            get key() {
                return JSON.stringify(this);
            }

            /**
             * the standard toJSON for JSON.stringify() call
             * @returns {{type: *, method: *, url: *, data: null}}
             */
            toJSON() {
                let obj = {
                    type: this.type,
                    method: this.method,
                    url: this.url,
                    data: this.data || null,
                };

                if (this.triggerer) {
                    obj.trigger = {element: _elementToString(this.triggerer.element), event: this.triggerer.eventName};
                }

                return obj;
            }

        }

        /**
         * Class PageEvent
         * Element's event found in the page
         */
        class PageEvent {
            /**
             * @param {Element} element
             * @param {String} eventName
             * @constructor
             */
            constructor(element, eventName) {
                /**
                 * the DOM element
                 * @type {Element}
                 */
                this.element = element;
                /**
                 * the event name
                 * @type {String}
                 */
                this.eventName = eventName;
            }

            /**
             * Trigger the page event
             */
            trigger() {

                console.debug('PageEvent triggering events for : ', _elementToString(this.element), this.eventName);

                let event = new Event(this.eventName);

                if (PageEvent.EVENT_NAME_TO_EVENT_TYPE[this.eventName]) {
                    event = new PageEvent.EVENT_NAME_TO_EVENT_TYPE[this.eventName](this.eventName);
                }
                this.element.dispatchEvent(event);
            }
        }

        /**
         * constant EVENT_NAME_TO_EVENT_TYPE
         * see: https://developer.mozilla.org/en-US/docs/Web/Events#Standard_events
         */
        Object.defineProperty(PageEvent, 'EVENT_NAME_TO_EVENT_TYPE', {
            writable: false,
            enumerable: true,
            configurable: false,
            value: {
                'compositionend': CompositionEvent,
                'compositionstart': CompositionEvent,
                'compositionupdate': CompositionEvent,
                'drag': DragEvent,
                'dragend': DragEvent,
                'dragenter': DragEvent,
                'dragleave': DragEvent,
                'dragover': DragEvent,
                'dragstart': DragEvent,
                'drop': DragEvent,
                'hashchange': HashChangeEvent,
                'keydown': KeyboardEvent,
                'keypress': KeyboardEvent,
                'keyup': KeyboardEvent,
                'messageerror': MessageEvent,
                'message': MessageEvent,
                'click': MouseEvent,
                'dblclick': MouseEvent,
                'mousedown': MouseEvent,
                'mouseenter': MouseEvent,
                'mouseleave': MouseEvent,
                'mousemove': MouseEvent,
                'mouseout': MouseEvent,
                'mouseover': MouseEvent,
                'mouseup': MouseEvent,
                'contextmenu': MouseEvent,
                'show': MouseEvent,
                'pagehide': PageTransitionEvent,
                'pageshow': PageTransitionEvent,
                'gotpointercapture': PointerEvent,
                'lostpointercapture': PointerEvent,
                'pointercancel': PointerEvent,
                'pointerdown': PointerEvent,
                'pointerenter': PointerEvent,
                'pointerleave': PointerEvent,
                'pointermove': PointerEvent,
                'pointerout': PointerEvent,
                'pointerover': PointerEvent,
                'pointerup': PointerEvent,
                'popstate': PopStateEvent,
                'progress': ProgressEvent,
                'loadend': ProgressEvent,
                'loadstart': ProgressEvent,
                'timeout': ProgressEvent,
                'touchcancel': TouchEvent,
                'touchend': TouchEvent,
                'touchmove': TouchEvent,
                'touchstart': TouchEvent,
                'transitionend': TransitionEvent,
                'abort': UIEvent,
                'error': UIEvent,
                'load': UIEvent,
                'resize': UIEvent,
                'scroll': UIEvent,
                'select': UIEvent,
                'unload': UIEvent,
                'wheel': WheelEvent,
            },
        });

        //see: https://developer.mozilla.org/en-US/docs/Web/Events#Standard_events

        /**
         * Class Probe
         */
        class Probe {
            /**
             * @param options
             * @constructor
             */
            constructor(options) {
                this._options = options;

                this.sentXHRs = [];

                this.eventLoopManager = new EventLoopManager(this, window.__PROBE_CONSTANTS__.eventLoopConfig);

                this.seenRequest = [];
                this._currentPageEvent = undefined;
                this._eventsMap = [];
                this._triggeredPageEvents = [];
            }

            printRequest(request) {
                let requestKey = request.key;
                if (this.seenRequest.indexOf(requestKey) < 0) {
                    this.seenRequest.push(requestKey);
                    window.__PROBE_FN_RETURN_REQUEST__(['request', request]);
                }
            }

            printJSONP(node) {

                if (node.nodeName.toLowerCase() === 'script' && node.hasAttribute('src')) {
                    try {
                        let u = _getAbsoluteUrl(node.getAttribute('src'));

                        // JSONP must have a querystring...
                        if (u.search && u.search !== '') {
                            let req = new Request('jsonp', 'GET', u.href, null, this.getLastTriggerPageEvent());
                            this.printRequest(req);
                        }
                    } catch (e) {
                        console.warn(e.message);
                    }

                }
            }

            printLink(url) {
                try {
                    let req = new Request('link', 'GET', url, undefined, this.getLastTriggerPageEvent());
                    this.printRequest(req);
                } catch (e) {
                    console.warn(e.message);
                }
            }

            printWebsocket(url) {
                try {
                    let req = new Request('websocket', 'GET', url, null, this.getLastTriggerPageEvent());
                    this.printRequest(req);
                } catch (e) {
                    console.warn(e.message);
                }
            }

            getRandomValue(type) {
                if (!(type in this._options.inputValues)) {
                    type = 'string';
                }

                return this._options.inputValues[type];
            }

            /**
             * return the last element/event name pair triggered
             * @returns {PageEvent}
             */
            getLastTriggerPageEvent() {
                return this._currentPageEvent;
            }

            /**
             * get request from the given FORM element
             * @param {Element} form
             * @returns {Request}
             */
            getFormAsRequest(form) {
                let par, req,
                    formObj = {};

                formObj.method = form.getAttribute('method');
                if (!formObj.method) {
                    formObj.method = 'GET';
                } else {
                    formObj.method = formObj.method.toUpperCase();
                }

                formObj.url = form.getAttribute('action');
                if (!formObj.url) {
                    formObj.url = document.location.href;
                }
                formObj.data = [];
                let inputs = form.querySelectorAll('input, select, textarea');
                for (let a = 0; a < inputs.length; a++) {
                    if (!inputs[a].name) {
                        continue;
                    }
                    par = encodeURIComponent(inputs[a].name) + '=' + encodeURIComponent(inputs[a].value);
                    if (inputs[a].tagName === 'INPUT' && inputs[a].type !== null) {

                        switch (inputs[a].type.toLowerCase()) {
                            case 'button':
                            case 'submit':
                                break;
                            case 'checkbox':
                            case 'radio':
                                if (inputs[a].checked) {
                                    formObj.data.push(par);
                                }
                                break;
                            default:
                                formObj.data.push(par);
                        }

                    } else {
                        formObj.data.push(par);
                    }
                }

                formObj.data = formObj.data.join('&');

                if (formObj.method === 'GET') {
                    let url = _getAbsoluteUrl(formObj.url);
                    url.search = formObj.data;
                    req = new Request('form', 'GET', url.href);
                } else {
                    req = new Request('form', 'POST', formObj.url, formObj.data);
                }

                return req;
            }

            /**
             * add the given element/event pair to map
             * @param {Element} element
             * @param {String} eventName
             */
            addEventToMap(element, eventName) {
                let isFound = false;

                // searching for the existing element/event pair in the map
                for (let i = 0; i < this._eventsMap.length && !isFound; i++) {
                    if (this._eventsMap[i].element === element) {
                        this._eventsMap[i].events.push(eventName);
                        isFound = true;
                    }
                }

                // if no pair found, create it
                if (!isFound) {
                    this._eventsMap.push({
                        element: element,
                        events: [eventName],
                    });
                }
            }

            /**
             * Start the analysis of the current Document
             */
            startAnalysis() {

                // Parsing the current DOM
                let elements = document.getElementsByTagName('*');
                for (let i = 0; i < elements.length; i++) {
                    let element = elements[i];
                    if (element.nodeType === Node.ELEMENT_NODE) {
                        this.eventLoopManager.scheduleDOMAssessment(element);
                    }
                }

                // starting the eventLoop manager
                this.eventLoopManager.start();
            }


            /**
             * @param {Element} element
             * @private
             */
            _setVal(element) {
                let _this = this;

                const setv = function(name) {
                    let ret = _this.getRandomValue('string');
                    window.__PROBE_CONSTANTS__.inputNameMatchValue.forEach(function(matchValue) {
                        let regexp = new RegExp(matchValue.name, 'gi');
                        if (name.match(regexp)) {
                            ret = _this.getRandomValue(matchValue.value);
                        }
                    });
                    return ret;
                };

                // needed by angularjs and other single page app code
                const triggerChange = function() {
                    // update angular model
                    _this._trigger(new PageEvent(element, 'input'));

                    // _this._trigger(new PageEvent(element, 'blur'));
                    // _this._trigger(new PageEvent(element, 'keyup'));
                    // _this._trigger(new PageEvent(element, 'keydown'));
                };

                if (element.tagName.toLowerCase() === 'textarea') {
                    element.value = setv(element.name);
                    triggerChange();

                } else if (element.tagName.toLowerCase() === 'select') {
                    let opts = element.getElementsByTagName('option');
                    if (opts.length > 1) { // avoid to set the first (already selected) options
                        // @TODO .. qui seleziono l'ultimo val.. ma devo controllare che non fosse "selected"
                        //TODO: .. here I select the last value.. but I have to check that it was not "selected"
                        element.value = opts[opts.length - 1].value;
                    } else {
                        element.value = setv(element.name);
                    }
                    triggerChange();

                } else if (element.tagName.toLowerCase() === 'input') {
                    let type = element.type.toLowerCase();

                    switch (type) {
                        case 'button':
                        case 'hidden':
                        case 'submit':
                        case 'file':
                            return;
                        case '':
                        case 'text':
                        case 'search':
                            element.value = setv(element.name);
                            break;
                        case 'radio':
                        case 'checkbox':
                            element.setAttribute('checked', !(element.getAttribute('checked')));
                            break;
                        case 'range':
                        case 'number':
                            if ('min' in element && element.min) {
                                element.value = (parseInt(element.min) + parseInt(('step' in element) ? element.step : 1));
                            } else {
                                element.value = parseInt(this.getRandomValue('number'));
                            }
                            break;
                        case 'password':
                        case 'color':
                        case 'date':
                        case 'email':
                        case 'month':
                        case 'time':
                        case 'url':
                        case 'week':
                        case 'tel':
                            element.value = this.getRandomValue(type);
                            break;
                        case 'datetime-local':
                            element.value = this.getRandomValue('datetimeLocal');
                            break;
                        default:
                            return;
                    }

                    triggerChange();
                }
            }

            /**
             * schedule the trigger of the given event on the given element when the eventLoop is ready
             *
             * @param {PageEvent} pageEvent which have to be triggered
             * @private
             */
            _trigger(pageEvent) {

                // trigger the given event only when there is some space in the event stack to avoid collision
                // and give time to things to resolve properly (since we trigger user driven event,
                // it is important to give time to the analysed page to breath between calls)
                this.eventLoopManager.scheduleEventTriggering(pageEvent);
            }

            /**
             * @param  {Element} element
             * @returns {Array}
             * @private
             */
            _getEventsForElement(element) {
                let events = [],
                    map = this._eventsMap;

                for (let a = 0; a < map.length; a++) {
                    if (map[a].element === element) {
                        events = map[a].events.slice();
                        break;
                    }
                }

                for (let selector in window.__PROBE_CONSTANTS__.triggerableEvents) {
                    if (element.webkitMatchesSelector(selector)) {
                        events = events.concat(window.__PROBE_CONSTANTS__.triggerableEvents[selector]);
                    }
                }

                return events;
            }

            /**
             * Request trigger all event for a given element
             * @param {Element} element
             * @private
             */
            _triggerElementEvents(element) {
                let events = this._getEventsForElement(element);

                events.forEach(eventName => {
                    let pageEvent = new PageEvent(element, eventName);

                    console.debug('triggering events for : ' + _elementToString(element) + ' ' + eventName);

                    if (!['load', 'unload', 'beforeunload'].includes(eventName) && !this._isPageEventAlreadyTriggered(pageEvent)) {
                        this._triggeredPageEvents.push(pageEvent);
                        this._trigger(pageEvent);
                    }
                });
            }

            /**
             * @param pageEvent
             * @returns {boolean}
             * @private
             */
            _isPageEventAlreadyTriggered(pageEvent) {
                let array = this._triggeredPageEvents, result = false;
                if (array.length >= 1) {
                    for (let i = 0; (i < array.length && !result); i++) {
                        let isAllKeysFound = true;
                        for (let key in array[i]) {
                            if (array[i][key] !== pageEvent[key]) {
                                isAllKeysFound = false;
                            }
                        }
                        if (isAllKeysFound) {
                            result = true;
                        }
                    }
                }
                return result;
            }

            /**
             * @param {Element} element
             * @private
             */
            _mapElementEvents(element) {
                window.__PROBE_CONSTANTS__.mappableEvents.forEach(eventName => {
                    let onEventName = 'on' + eventName;

                    if (onEventName in element && element[onEventName]) {
                        this.addEventToMap(element, eventName);
                    }
                });
            }

            /**
             * print request from <form> html tag
             * @param {Element} element
             * @private
             */
            _printRequestFromForm(element) {
                if (element.tagName.toLowerCase() === 'form') {
                    try {
                        this.printRequest(this.getFormAsRequest(element));
                    } catch (e) {
                        console.warn(e.message);
                    }
                }
            }

            /**
             * print request from <a> html tag
             * @param {Element} element
             * @private
             */
            _printRequestFromATag(element) {
                if (element.tagName.toLowerCase() === 'a' && element.hasAttribute('href')) {
                    this.printLink(element.href);
                }
            }

            /**
             * analyze the given element
             * @param {Element} element - the element to analyze
             * @private
             */
            _analyzeDOMElement(element) {

                // map property events and fill input values
                this._mapElementEvents(element);

                if (this._options.fillValues) {
                    // Parsing the current element and set values for each element within
                    let elements = element.getElementsByTagName('*');
                    for (let i = 0; i < elements.length; i++) {
                        this._setVal(elements[i]);
                    }
                }

                this._printRequestFromForm(element);
                this._printRequestFromATag(element);

                if (this._options.triggerEvents) {
                    this._triggerElementEvents(element);
                }
            }
        }

        /**
         * return an absolute url for the provided url string
         * @param url
         * @param clearHash - remove the hash part if true
         * @return {URL}
         */
        function _getAbsoluteUrl(url, clearHash) {
            let u = new URL(url, window.location.href);

            if (!u.protocol.match(/^(https?:|s?ftp:|javascript:)/)) {
                throw new TypeError(`Wrong protocol type: ${u.protocol}`);
            }
            if (clearHash === true) {
                u.hash = '';
            }
            return u;
        }

        /**
         * convert an element to a string
         * @param {Element=} element - element to convert
         * @returns {string}
         * @private
         * @static
         */
        function _elementToString(element) {
            let str = '[]';

            if (element) {

                let tagName = (element === document ? 'DOCUMENT' : (element === window ? 'WINDOW' : element.tagName)),
                    text = undefined,
                    className = element.className ? (element.className.indexOf(' ') !== -1 ? '\'' + element.className + '\'' : element.className) : '';

                if (element.textContent) {
                    text = element.textContent.trim()
                        .replace(/\s/, ' ')
                        .substring(0, 10);

                    if (text.includes(' ')) {
                        text = '\'' + text + '\'';
                    }
                }


                str = '[' +
                    (tagName ? tagName + ' ' : '') +
                    (element.name && typeof element.name === 'string' ? element.name + ' ' : '') +
                    (className ? '.' + className + ' ' : '') +
                    (element.id ? '#' + element.id + ' ' : '') +
                    (element.src ? 'src=' + element.src + ' ' : '') +
                    (element.action ? 'action=' + element.action + ' ' : '') +
                    (element.method ? 'method=' + element.method + ' ' : '') +
                    (element.value ? 'value=' + element.value + ' ' : '') +
                    (text ? 'txt=' + text : '') +
                    ']';
            }
            return str;
        }

        function _initializeProbeHook(excludedUrls, overrideTimeoutFunctions, XHRTimeout) {

            Node.prototype.__originalAddEventListener = Node.prototype.addEventListener;
            Node.prototype.addEventListener = function() {
                // if event is note related to content loading
                // see: https://developer.mozilla.org/en-US/docs/Web/Events
                if (!['DOMContentLoaded', 'readystatechange'].includes(arguments[0])) {
                    window.__PROBE__.addEventToMap(this, arguments[0]);
                }
                this.__originalAddEventListener.apply(this, arguments);
            };

            window.__originalAddEventListener = window.addEventListener;
            window.addEventListener = function() {
                // if event is not related to 'load' event
                // see: https://developer.mozilla.org/en-US/docs/Web/Events
                if (!['load', 'unload', 'beforeunload'].includes(arguments[0])) {
                    window.__PROBE__.addEventToMap(this, arguments[0]);
                }
                window.__originalAddEventListener.apply(this, arguments);
            };

            XMLHttpRequest.prototype.__originalOpen = XMLHttpRequest.prototype.open;
            XMLHttpRequest.prototype.open = function(method, url, async, user, password) {
                try {
                    this.__request = new Request('xhr', method, url);

                    // adding XHR listener
                    this.addEventListener('readystatechange', function() {
                        // if not finish, it's open
                        // https://developer.mozilla.org/en-US/docs/Web/API/XMLHttpRequest/readyState
                        if (this.readyState >= 1 && this.readyState < 4) {
                            window.__PROBE__.eventLoopManager.sentXHR(this);
                        } else if (this.readyState === 4) {
                            // /!\ DONE means that the XHR finish but could have FAILED
                            window.__PROBE__.eventLoopManager.doneXHR(this);
                        }
                    });

                    this.timeout = XHRTimeout;
                } catch (e) {
                    console.warn(e.message);
                }
                return this.__originalOpen(method, url, async, user, password);

            };

            XMLHttpRequest.prototype.__originalSend = XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.send = function(data) {
                this.__request.data = data;
                this.__request.triggerer = window.__PROBE__.getLastTriggerPageEvent();

                try {
                    let absoluteUrl = _getAbsoluteUrl(this.__request.url).href;
                    excludedUrls.forEach((url) => {
                        if (absoluteUrl.match(url)) {
                            this.__skipped = true;
                        }
                    });
                } catch (e) {
                    console.warn(e.message);
                    this.__skipped = true;
                }

                // check if request has already been sent
                let requestKey = this.__request.key;
                if (window.__PROBE__.sentXHRs.indexOf(requestKey) !== -1) {
                    return;
                }

                window.__PROBE__.sentXHRs.push(requestKey);
                window.__PROBE__.printRequest(this.__request);

                if (!this.__skipped) {
                    return this.__originalSend(data);
                }
            };

            Node.prototype.__originalAppendChild = Node.prototype.appendChild;
            Node.prototype.appendChild = function(node) {
                window.__PROBE__.printJSONP(node);
                return this.__originalAppendChild(node);
            };

            Node.prototype.__originalInsertBefore = Node.prototype.insertBefore;
            Node.prototype.insertBefore = function(node, element) {
                window.__PROBE__.printJSONP(node);
                return this.__originalInsertBefore(node, element);
            };

            Node.prototype.__originalReplaceChild = Node.prototype.replaceChild;
            Node.prototype.replaceChild = function(node, oldNode) {
                window.__PROBE__.printJSONP(node);
                return this.__originalReplaceChild(node, oldNode);
            };

            window.WebSocket = (function(WebSocket) {
                return function(url) {
                    window.__PROBE__.printWebsocket(url);
                    return WebSocket.prototype;
                };
            })(window.WebSocket);

            if (overrideTimeoutFunctions) {
                window.__originalSetTimeout = window.setTimeout;
                window.setTimeout = function() {
                    // Forcing a delay of 0
                    arguments[1] = 0;
                    return window.__originalSetTimeout.apply(this, arguments);
                };

                window.__originalSetInterval = window.setInterval;
                window.setInterval = function() {
                    // Forcing a delay of 0
                    arguments[1] = 0;
                    return window.__originalSetInterval.apply(this, arguments);
                };

            }

            HTMLFormElement.prototype.__originalSubmit = HTMLFormElement.prototype.submit;
            HTMLFormElement.prototype.submit = function() {
                try {
                    window.__PROBE__.printRequest(window.__PROBE__.getFormAsRequest(this));
                } catch
                (e) {
                    console.warn(e.message);
                }
                return this.__originalSubmit();
            };

            // prevent window.close
            window.close = function() {
            };

            window.open = function(url) {
                window.__PROBE__.printLink(url);
            };

            // create an observer instance for DOM changes
            let observer = new WebKitMutationObserver(function(mutations) {
                window.__PROBE__.eventLoopManager.nodeMutated(mutations);
            });
            let eventAttributeList = ['src', 'href'];
            window.__PROBE_CONSTANTS__.mappableEvents.forEach(function(event) {
                eventAttributeList.push('on' + event);
            });
            // observing for any change on document and its children
            observer.observe(window.document.documentElement, {
                childList: true,
                attributes: true,
                characterData: false,
                subtree: true,
                characterDataOldValue: false,
                attributeFilter: eventAttributeList,
            });
        }

        if (!window.__PROBE_CONSTANTS__ && window.location.href.startsWith('http')) {

            console.info(`setting the probe on ${window.location.href}`);

            // adding constants to page
            window.__PROBE_CONSTANTS__ = constants;

            let probe = new Probe(options);

            // listening for messageEvent to trigger waiting events
            window.addEventListener('message', probe.eventLoopManager.eventMessageHandler.bind(probe.eventLoopManager), true);


            window.__PROBE__ = probe;

            // initialize the hook once the DOM content is fully loaded
            document.addEventListener('DOMContentLoaded', () => {
                _initializeProbeHook(options.excludedUrls, options.overrideTimeoutFunctions, constants.XHRTimeout);
            });
        }
    };


})();
