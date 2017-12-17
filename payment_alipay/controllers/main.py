# -*- coding: utf-8 -*-

try:
    import simplejson as json
except ImportError:
    import json
import logging
import pprint
import urllib2
import werkzeug

from odoo import SUPERUSER_ID
from odoo import http
from odoo.addons.payment_alipay.models import util
from odoo.http import request
from werkzeug.utils import redirect

_logger = logging.getLogger(__name__)


class AlipayController(http.Controller):
    _notify_url = '/payment/alipay/notify/'
    _return_url = '/payment/alipay/return/'
    _cancel_url = '/payment/alipay/cancel/'

    def alipay_validate_data(self, **post):
        res = False
        tx = False
        acquirer = False
        reference = post.get('out_trade_no')
        notify_id = post.get('notify_id')
        seller_id = post.get('seller_id')

        if reference:
            tx = request.env['payment.transaction'].search(
                [('reference', '=', reference)], limit=1
            )

        if tx:
            acquirer = tx.acquirer_id

        if acquirer:
            _KEY = acquirer.alipay_partner_key

        if not _KEY:
            return False

        _, prestr = util.params_filter(post)
        mysign = util.build_mysign(prestr, _KEY, 'MD5')
        if mysign != post.get('sign'):
            return False

        alipay_urls = request.env['payment.acquirer']._get_alipay_urls(
            tx and tx.acquirer_id and tx.acquirer_id.environment or 'prod'
        )
        validate_url = alipay_urls['alipay_url']
        new_post = {
            'service': 'notify_verify',
            'partner': seller_id,
            'notify_id': notify_id,
        }
        urequest = urllib2.Request(validate_url, werkzeug.url_encode(new_post))
        uopen = urllib2.urlopen(urequest)
        resp = uopen.read()
        if resp == 'true':
            _logger.info('Alipay: validated data')
            res = True

        else:
            _logger.warning(
                'Alipay: unrecognized alipay answer, received %s instead of VERIFIED or INVALID'
                % resp.text
            )
        return res

    @http.route(
        '/payment/alipay/notify', type='http', auth='none', methods=['POST']
    )
    def alipay_notify(self, **post):
        """ Alipay Notify. """
        _logger.info(
            'Beginning Alipay notify form_feedback with post data %s',
            pprint.pformat(post)
        )  # debug
        if self.alipay_validate_data(**post):
            result = request.env['payment.transaction'].sudo().form_feedback(
                post, 'alipay'
            )

            if result:
                return 'success'
            else:
                return ''

        else:
            return ''

    @http.route(
        '/payment/alipay/return', type='http', auth="none", methods=['GET']
    )
    def alipay_return(self, **get):
        """ Alipay Return """
        _logger.info(
            'Beginning Alipay return form_feedback with post data %s',
            pprint.pformat(get)
        )  # debug
        if self.alipay_validate_data(**get):
            reference = get.get('out_trade_no')
            if reference:
                tx = request.env['payment.transaction'].search(
                    [('reference', '=', reference)], limit=1
                )
                if get.get('is_success') == 'T':
                    tx.sudo().write({'state': 'pending'})

            return redirect('/shop/confirmation')

    @http.route('/payment/alipay/cancel', type='http', auth="none")
    def alipay_cancel(self, **post):
        """ When the user cancels its Alipay payment: GET on this route """
        _logger.info(
            'Beginning Alipay cancel with post data %s', pprint.pformat(post)
        )  # debug

        return ''
