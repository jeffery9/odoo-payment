# -*- coding: utf-8 -*-

try:
    import simplejson as json
except ImportError:
    import json
import logging
import pprint
import urllib2
import werkzeug

from lxml import etree

from odoo import SUPERUSER_ID
from odoo import http
from odoo.addons.payment_weixin.models import util
from odoo.http import request
from werkzeug.utils import redirect

_logger = logging.getLogger(__name__)


class WeixinController(http.Controller):
    _notify_url = '/payment/weixin/notify/'
    _code_url = '/payment/weixin/code_url/'

    def weixin_validate_data(self, xml):
        json = {}
        for el in etree.fromstring(xml):
            json[el.tag] = el.text

        acquirer = request.env['payment.acquirer'].search(
            [('weixin_appid', '=', json.get('appid'))], limit=1
        )

        _KEY = acquirer.weixin_key
        _logger.info('weixin key: %s' % _KEY)
        if not _KEY:
            return False

        _, prestr = util.params_filter(json)
        mysign = util.build_mysign(prestr, _KEY, 'MD5')
        if mysign != json.get('sign'):
            return False

        _logger.info('weixin: data validated!')
        return True

    @http.route(
        '/payment/weixin/notify',
        type='http',
        auth='none',
        csrf=False,
        methods=['POST']
    )
    def weixin_notify(self, **post):
        """ weixin Notify. """
        xml = request.httprequest.data
        _logger.info(
            'Beginning weixin notify form_feedback with post data %s',
            pprint.pformat(xml)
        )  # debug
        if len(xml) == 0:
            return '<xml><return_code><![CDATA[FAIL]]></return_code></xml>'

        if self.weixin_validate_data(xml):
            return request.env['payment.transaction'].sudo().form_feedback(
                json,
                'weixin',
            )

        else:
            return '<xml><return_code><![CDATA[FAIL]]></return_code></xml>'

    @http.route(
        '/payment/weixin/code_url', type='http', auth='none', csrf=False
    )
    def weixin_qrcode(self, **post):
        _logger.info(
            'Beginning weixin_qrcode with post data %s', pprint.pformat(post)
        )  # debug

        code_url = request.env['payment.acquirer']._gen_weixin_code_url(post)

        if code_url:
            _logger.info('Weixin code_url %s', code_url)

            tx_id = request.env['payment.transaction'].search(
                [('reference', '=', post['out_trade_no'])]
            )

            # tx_id.sudo().write({'weixin_txn_code_url': code_url, 'state': 'pending'})
            tx_id.sudo().write({'weixin_txn_code_url': code_url})

            return redirect('/shop/confirmation')

        else:
            return redirect('/shop')
