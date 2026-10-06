import unittest
from unittest.mock import AsyncMock, patch

from gateway import GatewayError, retry_region, transient_report_error


class GatewayRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_failed_region_restarts_and_partial_attempt_is_discarded(self):
        run = AsyncMock(side_effect=[GatewayError('TX: Gateway report navigation failed: TimeoutError', retryable=True),
                                    [{'store': '3910-01', 'total_tips': '12.00'}]])
        with patch('gateway.asyncio.sleep', new_callable=AsyncMock):
            result = await retry_region('TX', run)
        self.assertEqual(result, [{'store': '3910-01', 'total_tips': '12.00'}])
        self.assertEqual([call.args for call in run.call_args_list], [(1,), (2,)])

    async def test_retries_are_bounded_and_never_return_partial_success(self):
        run = AsyncMock(side_effect=GatewayError('TX: Gateway Financial View frame failed: TimeoutError', retryable=True))
        with patch('gateway.asyncio.sleep', new_callable=AsyncMock):
            with self.assertRaises(GatewayError):
                await retry_region('TX', run)
        self.assertEqual(run.await_count, 2)

    async def test_source_and_security_errors_do_not_retry(self):
        for message in ('Gateway report date did not match', 'Gateway selection is not exactly one requested store',
                        'Both Gateway tip lines must be present', 'Gateway report frame unavailable; check login/2FA'):
            run = AsyncMock(side_effect=GatewayError(message))
            with self.assertRaises(GatewayError):
                await retry_region('TX', run)
            self.assertEqual(run.await_count, 1)

    def test_retry_classification_is_transport_only_at_report_stages(self):
        PWTimeout = type('TimeoutError', (Exception,), {'__module__': 'playwright._impl._errors'})
        PWError = type('Error', (Exception,), {'__module__': 'playwright._impl._errors'})
        for stage in ('report navigation', 'Financial View frame', 'report refresh'):
            self.assertTrue(transient_report_error(PWTimeout('private browser details'), stage))
            self.assertTrue(transient_report_error(PWError('net::ERR_CONNECTION_RESET'), stage))
        for stage in ('login', 'date parameters', 'store selection', 'tip parsing'):
            self.assertFalse(transient_report_error(PWTimeout('timeout'), stage))
        self.assertFalse(transient_report_error(ValueError('invalid tip amount'), 'report refresh'))
        self.assertFalse(transient_report_error(PWError('strict mode violation'), 'report refresh'))


if __name__ == '__main__':
    unittest.main()
