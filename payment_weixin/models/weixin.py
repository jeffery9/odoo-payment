# -*- coding: utf-'8' "-*-"

try:
    import simplejson as json
except ImportError:
    import json
import logging
import random
import string
import urllib2
import urlparse

from lxml import etree
from odoo import api
from odoo import fields
from odoo import models

from odoo.addons.payment_weixin.controllers.main import WeixinController
from odoo.exceptions import UserError, ValidationError
from odoo.http import request

import util

_logger = logging.getLogger(__name__)


class AcquirerWeixin(models.Model):
    _inherit = 'payment.acquirer'

    provider = fields.Selection(selection_add=[('weixin', 'Weixin')])

    weixin_appid = fields.Char(
        string=u'微信支付APPID', required_if_provider='weixin'
    )
    weixin_mch_id = fields.Char(
        string=u'微信支付商户号', required_if_provider='weixin'
    )
    weixin_key = fields.Char(
        string=u'微信支付API密钥', required_if_provider='weixin'
    )
    weixin_signkey = fields.Char(string=u'微信支付验签密钥')
    weixin_secret = fields.Char(
        string=u'微信支付Appsecret', required_if_provider='weixin'
    )
    ip_address = fields.Char(
        string='Public IP Address', required_if_provider='weixin'
    )

    def _get_weixin_urls(self, environment):
        if environment == 'prod':
            return {
                'weixin_url': 'https://api.mch.weixin.qq.com/pay/unifiedorder'
            }
        else:
            return {
                'weixin_url':
                    'https://api.mch.weixin.qq.com/sandboxnew/pay/unifiedorder'
            }

    @api.model
    def _get_weixin_key(self):
        return self.search([('provider', '=', 'weixin')], limit=1).weixin_key

    @api.model
    def _get_ipaddress(self):
        return self.search([('provider', '=', 'weixin')], limit=1).ip_address

    def json2xml(self, json):
        string = ""
        for k, v in json.items():
            string = string + "<%s>" % (k) + str(v) + "</%s>" % (k)

        return string

    def _try_url(self, request, tries=3):

        done, res = False, None
        while (not done and tries):
            try:
                res = urllib2.urlopen(request)
                done = True
            except urllib2.HTTPError as e:
                res = e.read()
                e.close()
                if tries and res and json.loads(res)[
                    'name'
                ] == 'INTERNAL_SERVICE_ERROR':
                    _logger.warning(
                        'Failed contacting Weixin, retrying (%s remaining)' %
                        tries
                    )
            tries = tries - 1
        if not res:
            pass
            # raise openerp.exceptions.
        result = res.read()
        res.close()
        return result

    def random_generator(
        self, size=6, chars=string.ascii_uppercase + string.digits
    ):
        return ''.join([random.choice(chars) for n in xrange(size)])

    @api.multi
    def weixin_form_generate_values(self, tx_values):
        weixin_tx_values = dict(tx_values)
        amount = int(tx_values.get('amount', 0) * 100)
        nonce_str = self.random_generator()
        base_url = self.env['ir.config_parameter'].get_param('web.base.url')
        url = self._get_weixin_urls(self.environment)['weixin_url']

        order_name = tx_values['reference']
        order = self.env['sale.order'].search([('name', '=', order_name)])
        order_lines = self.env['sale.order.line'].search_read(
            [('order_id', '=', order.id)], ['product_id', 'product_uom_qty']
        )

        string = u''
        for line in order_lines:
            product_name = line['product_id'][1]
            string += u'品名：%s   数量：%s   /  \n' % (
                product_name, line['product_uom_qty']
            )

        weixin_tx_values.update(
            {
                'appid':
                    self.weixin_appid,
                'mch_id':
                    self.weixin_mch_id,
                'key':
                    self.weixin_key,
                'nonce_str':
                    nonce_str,
                'body':
                    order_name,
                'detail':
                    string,
                'out_trade_no':
                    order_name,
                'total_fee':
                    amount,
                'amount':
                    tx_values['amount'],
                'spbill_create_ip':
                    self.ip_address,
                'notify_url':
                    '%s' %
                    urlparse.urljoin(base_url, WeixinController._notify_url),
                'trade_type':
                    'NATIVE',
                'weixin_url':
                    url,
            }
        )
        _logger.info("----weixin tx_values is %s" % (weixin_tx_values))
        return weixin_tx_values

    @api.model
    def weixin_get_form_action_url(self):
        return '/payment/weixin/code_url'

    @api.model
    def _get_weixin_signkey(self, acquirer):
        url = 'https://api.mch.weixin.qq.com/sandboxnew/pay/getsignkey'
        nonce_str = self.random_generator()
        data = {}
        data.update({'mch_id': acquirer.weixin_mch_id, 'nonce_str': nonce_str})

        _, prestr = util.params_filter(data)
        key = acquirer.weixin_key
        _logger.info("+++ prestr %s, Weixin Key %s" % (prestr, key))
        data['sign'] = util.build_mysign(prestr, key, 'MD5')

        data_xml = "<xml>" + self.json2xml(data) + "</xml>"

        request = urllib2.Request(url, data_xml)
        result = self._try_url(request, tries=3)

        _logger.info(
            "_______get_weixin_signkey_____ request to %s and the request data is %s, and request result is %s"
            % (url, data_xml, result)
        )
        return_xml = etree.fromstring(result)

        if return_xml.find('return_code').text == "SUCCESS" and return_xml.find(
            'sandbox_signkey'
        ).text != False:
            sandbox_signkey = return_xml.find('sandbox_signkey').text
        else:
            return_code = return_xml.find('return_code').text
            return_msg = return_xml.find('return_msg').text
            raise UserError("%s, %s" % (return_code, return_msg))

        return sandbox_signkey

    @api.multi
    def _gen_weixin_code_url(self, post_data):
        data = {}
        data.update(
            {
                'appid': post_data['appid'],
                'mch_id': post_data['mch_id'],
                'nonce_str': post_data['nonce_str'],
                'body': post_data['body'],
                'out_trade_no': post_data['out_trade_no'],
                'total_fee': post_data['total_fee'],
                'spbill_create_ip': post_data['spbill_create_ip'],
                'notify_url': post_data['notify_url'],
                'trade_type': post_data['trade_type'],
            }
        )

        acquirer = self.search([('weixin_appid', '=', post_data['appid'])])
        _logger.info("--- acquirer %s" % (acquirer))

        if acquirer.environment == 'prod':
            key = acquirer.weixin_key
        else:
            key = self._get_weixin_signkey(acquirer)

        _, prestr = util.params_filter(data)

        _logger.info("+++ prestr %s, Weixin Key %s" % (prestr, key))

        data['sign'] = util.build_mysign(prestr, key, 'MD5')

        data_xml = "<xml>" + self.json2xml(data) + "</xml>"

        url = acquirer._get_weixin_urls(acquirer.environment)['weixin_url']

        request = urllib2.Request(url, data_xml)
        result = self._try_url(request, tries=3)

        _logger.info(
            "________gen_weixin_code_url_____ request to %s and the request data is %s, and request result is %s"
            % (url, data_xml, result)
        )
        return_xml = etree.fromstring(result)

        data_json = {}

        for el in return_xml:
            data_json[el.tag] = el.text

        if data_json['return_code'] == "SUCCESS" and data_json.get(
            'code_url', False
        ):
            return data_json['code_url']

        else:
            return_code = data_json.get('return_code')
            return_msg = data_json.get('return_msg')

            msg = "[%s] %s " % (return_code, return_msg)

            _logger.info('+++ some error occurred %s' % msg)
            # raise UserError(msg)

        return False


class TxWeixin(models.Model):
    _inherit = 'payment.transaction'

    weixin_txn_id = fields.Char(string='Transaction ID')
    weixin_txn_type = fields.Char(string='Transaction type')
    weixin_txn_code_url = fields.Char(string='Weixin Pay Code URL')

    # --------------------------------------------------
    # FORM RELATED METHODS
    # --------------------------------------------------
    @api.model
    def _weixin_form_get_tx_from_data(self, data):
        reference = data.get('out_trade_no')
        txn_id = data.get('transaction_id')
        if not reference or not txn_id:
            error_msg = 'weixin: received data with missing reference (%s) or txn_id (%s)' % (
                reference, txn_id
            )
            _logger.error(error_msg)
            raise ValidationError(error_msg)

        tx = self.search([('reference', '=', reference)])
        if not tx or len(tx) > 1:
            error_msg = 'weixin: received data for reference %s' % (reference)
            if not tx:
                error_msg += '; no order found'
            else:
                error_msg += '; multiple order found'
            _logger.error(error_msg)
            raise ValidationError(error_msg)

        return tx

    @api.multi
    def _weixin_form_validate(self, data):
        status = data.get('result_code')
        data = {
            'acquirer_reference': data.get('out_trade_no'),
            'weixin_txn_id': data.get('transaction_id'),
            'weixin_txn_type': data.get('trade_type'),
        }

        if status == 'SUCCESS':
            _logger.info(
                'Validated weixin payment for tx %s: set as done' %
                (self.reference)
            )
            data.update(
                state='done',
                date_validate=data.get('time_end', fields.datetime.now())
            )
            self.write(data)
            return '<xml><return_code><![CDATA[SUCCESS]]></return_code></xml>'

        else:
            error = 'Received unrecognized status for weixin payment %s: %s, set as error' % (
                self.reference, status
            )
            _logger.info(error)
            data.update(state='error', state_message=error)
            self.write(data)
            return '<xml><return_code><![CDATA[FAIL]]></return_code></xml>'
