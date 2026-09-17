import type { Assessment, ErrorCharge } from '../types';

/** Review priority, not a prediction of approval or an allegation about a customer. */
export function assess(charge: ErrorCharge, _all: ErrorCharge[]): Assessment {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const deadline = charge.disputeDeadline ? new Date(charge.disputeDeadline + 'T23:59:59') : null;
  const daysLeft = deadline && Number.isFinite(deadline.getTime())
    ? Math.floor((deadline.getTime() - today.getTime()) / 86400000) : NaN;
  if (!Number.isFinite(charge.amount) || charge.amount <= 0) {
    return { disputable: false, priority: 'none', recoverable: 0, daysLeft, reasons: ['A valid positive charge amount is required.'] };
  }
  if (daysLeft < 0) {
    return { disputable: false, priority: 'none', recoverable: 0, daysLeft, reasons: ['The imported dispute deadline has passed.'] };
  }
  const reasons = ['Review the charge and supporting records before approving the draft.'];
  if (!Number.isFinite(daysLeft)) reasons.push('Deadline is unknown; check eligibility in the merchant portal.');
  else if (daysLeft <= 3) reasons.push(`${daysLeft} day(s) remain through the imported deadline.`);
  if (charge.amount >= 40) reasons.push('Higher charge amount: prioritize review.');
  return {
    disputable: true, priority: charge.amount >= 40 || daysLeft <= 3 ? 'high' : 'medium',
    recoverable: charge.amount, reasons, daysLeft,
  };
}
