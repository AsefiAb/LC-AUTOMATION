import type { ErrorCharge } from '../types';

export function draftDispute(charge: ErrorCharge): string {
  return [
    `Please review the $${charge.amount.toFixed(2)} error charge for order ${charge.orderId} at ${charge.storeName}.`,
    charge.itemsAffected.length ? `Reported items: ${charge.itemsAffected.join(', ')}.` : '',
    charge.customerNote ? `The imported record states: ${charge.customerNote}` : '',
    'Please review the supporting order, pickup, and delivery records and reverse any amount incorrectly attributed to the restaurant.',
  ].filter(Boolean).join('\n\n');
}
