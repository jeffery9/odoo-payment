# -*- coding: utf-'8' "-*-"

import base64

try:
    import simplejson as json
except ImportError:
    import json
import logging
import urlparse
import werkzeug.urls
import urllib2

from urllib import urlencode, urlopen

from odoo.addons.payment.models.payment_acquirer import ValidationError
from odoo.addons.payment_alipay.controllers.main import AlipayController
from odoo import models, fields, api, _
from odoo.tools.float_utils import float_compare
from odoo import SUPERUSER_ID, api

import util

_logger = logging.getLogger(__name__)


class AcquirerAlipay(models.Model):
    _inherit = 'payment.acquirer'

    ALIPAY_INTERFACE_TYPE = [
        ('create_direct_pay_by_user', 'Instant Payment Transaction'),
        ('create_partner_trade_by_buyer', 'Securied Transaction'),
    ]

    provider = fields.Selection(selection_add=[('alipay', 'Alipay')])
    alipay_partner_account = fields.Char(
        'Alipay Partner ID', required_if_provider='alipay'
    )
    alipay_partner_key = fields.Char(
        'Alipay Partner Key', required_if_provider='alipay'
    )
    alipay_seller_email = fields.Char(
        u'支付宝登录账号', required_if_provider='alipay'
    )
    alipay_interface_type = fields.Selection(
        ALIPAY_INTERFACE_TYPE, 'Interface Type', required_if_provider='alipay'
    )

    @api.model
    def _get_alipay_urls(self, environment):
        """ Alipay URLS """
        if environment == 'prod':
            return {'alipay_url': 'https://mapi.alipay.com/gateway.do?'}
        else:
            return {'alipay_url': 'https://openapi.alipaydev.com/gateway.do?'}

    @api.model
    def _get_alipay_partner_key(self):
        acquirer = request.env['payment.acquirer'].search(
            [('provider', '=', 'alipay')], limit=1
        )
        return acquirer.alipay_partner_key

    @api.multi
    def alipay_compute_fees(self, amount, currency_id, country_id):
        """ Compute alipay fees.

            :param float amount: the amount to pay
            :param integer country_id: an ID of a res.country, or None. This is
                                       the customer's country, to be compared to
                                       the acquirer company country.
            :return float fees: computed fees
        """
        if not self.fees_active:
            return 0.0
        country = self.env['res.country'].browse(country_id)
        if country and self.company_id.country_id.id == country.id:
            percentage = self.fees_dom_var
            fixed = self.fees_dom_fixed
        else:
            percentage = self.fees_int_var
            fixed = self.fees_int_fixed
        fees = (percentage / 100.0 * amount + fixed) / (1 - percentage / 100.0)
        return fees

    @api.multi
    def alipay_form_generate_values(self, tx_values):
        base_url = self.env['ir.config_parameter'
                            ].sudo().get_param('web.base.url')

        alipay_tx_values = dict(tx_values)
        alipay_tx_values.update(
            {
                'partner':
                    self.alipay_partner_account,
                'seller_email':
                    self.alipay_seller_email,
                'seller_id':
                    self.alipay_partner_account,
                '_input_charset':
                    'UTF-8',
                'out_trade_no':
                    tx_values['reference'],
                'subject':
                    tx_values['reference'],
                'body':
                    tx_values['reference'],
                'payment_type':
                    '1',
                'return_url':
                    '%s' %
                    urlparse.urljoin(base_url, AlipayController._return_url),
                'notify_url':
                    '%s' %
                    urlparse.urljoin(base_url, AlipayController._notify_url),
            }
        )

        to_sign = {}
        to_sign.update(
            {
                'partner':
                    self.alipay_partner_account,
                'seller_email':
                    self.alipay_seller_email,
                'seller_id':
                    self.alipay_partner_account,
                '_input_charset':
                    'UTF-8',
                'out_trade_no':
                    tx_values['reference'],
                'subject':
                    tx_values['reference'],
                'body':
                    tx_values['reference'],
                'payment_type':
                    '1',
                'return_url':
                    '%s' %
                    urlparse.urljoin(base_url, AlipayController._return_url),
                'notify_url':
                    '%s' %
                    urlparse.urljoin(base_url, AlipayController._notify_url),
            }
        )

        payload_direct = {
            'service': 'create_direct_pay_by_user',
            'total_fee': tx_values['amount'],
        }

        payload_escow = {
            'service': 'create_partner_trade_by_buyer',
            'logistics_type': 'EXPRESS',
            'logistics_fee': 0,
            'logistics_payment': 'SELLER_PAY',
            'price': tx_values['amount'],
            'quantity': 1,
        }

        if self.alipay_interface_type == 'create_direct_pay_by_user':
            to_sign.update(payload_direct)
            alipay_tx_values.update(payload_direct)

        if self.alipay_interface_type == 'create_partner_trade_by_buyer':
            to_sign.update(payload_escow)
            alipay_tx_values.update(payload_escow)

        _, prestr = util.params_filter(to_sign)
        alipay_tx_values['sign'] = util.build_mysign(
            prestr, self.alipay_partner_key, 'MD5'
        )
        alipay_tx_values['sign_type'] = 'MD5'

        _logger.info('----alipay tx_values is %s' % alipay_tx_values)

        return alipay_tx_values

    @api.multi
    def alipay_get_form_action_url(self):
        self.ensure_one()
        return self._get_alipay_urls(self.environment)['alipay_url']


class TxAlipay(models.Model):
    _inherit = 'payment.transaction'

    alipay_txn_id = fields.Char('Transaction ID')
    alipay_txn_type = fields.Char('Transaction type')

    # --------------------------------------------------
    # FORM RELATED METHODS
    # --------------------------------------------------

    @api.model
    def _alipay_form_get_tx_from_data(self, data):
        reference, txn_id = data.get('out_trade_no'), data.get('trade_no')
        if not reference or not txn_id:
            error_msg = 'Alipay: received data with missing reference (%s) or txn_id (%s)' % (
                reference, txn_id
            )
            _logger.error(error_msg)
            raise ValidationError(error_msg)

        tx = self.env['payment.transaction'].search(
            [('reference', '=', reference)]
        )
        if not tx or len(tx) > 1:
            error_msg = 'Alipay: received data for reference %s' % (reference)
            if not tx:
                error_msg += '; no order found'
            else:
                error_msg += '; multiple order found'
            _logger.error(error_msg)
            raise ValidationError(error_msg)
        return tx

    @api.multi
    def _alipay_form_validate(self, data):
        status = data.get('trade_status')
        data = {
            'acquirer_reference': data.get('out_trade_no'),
            'alipay_txn_id': data.get('trade_no'),
            'alipay_txn_type': data.get('payment_type'),
            'partner_reference': data.get('buyer_id')
        }
        acquirer = self.acquirer_id
        if acquirer.alipay_interface_type == 'create_direct_pay_by_user':
            if status in ['TRADE_FINISHED', 'TRADE_SUCCESS']:
                _logger.info(
                    'Validated Alipay payment for self %s: set as done' %
                    (self.reference)
                )
                data.update(
                    state='done',
                    date_validate=data.get(
                        'notify_time', fields.datetime.now()
                    )
                )
                return self.write(data)
            else:
                error = 'Received unrecognized status for Alipay payment %s: %s, set as error' % (
                    self.reference, status
                )
                _logger.info(error)
                data.update(state='error', state_message=error)
                return self.write(data)

        if acquirer.alipay_interface_type in [
            'create_partner_trade_by_buyer', 'trade_create_by_buyer'
        ]:
            if status in ['WAIT_SELLER_SEND_GOODS']:
                _logger.info(
                    'Validated Alipay payment for self %s: set as done' %
                    (self.reference)
                )
                data.update(
                    state='done',
                    date_validate=data.get(
                        'gmt_payment', fields.datetime.now()
                    )
                )
                return self.write(data)
            elif status in ['WAIT_BUYER_PAY']:
                _logger.info(
                    'Received notification for Alipay payment %s: set as pending'
                    % (self.reference)
                )
                data.update(
                    state='pending',
                    state_message=data.get('pending_reason', '')
                )
                return self.write(data)
            else:
                error = 'Received unrecognized status for Alipay payment %s: %s, set as error' % (
                    self.reference, status
                )
                _logger.info(error)
                data.update(state='error', state_message=error)
                return self.write(data)
