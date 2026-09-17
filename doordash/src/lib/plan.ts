import type { ErrorCharge } from '../types';

export interface PlanOrder {
  storeId: string; store: string; region: string; href: string;
  amountCents: number; reported: string; description: string; approved: boolean;
}
export interface ReviewPlan { version: 1; generatedAt: string; scope: string; orders: PlanOrder[] }

export function parsePlan(text: string): { plan: ReviewPlan; charges: ErrorCharge[] } {
  const plan = JSON.parse(text) as ReviewPlan;
  if (plan.version !== 1 || !Array.isArray(plan.orders)) throw new Error('This is not a DoorDash review plan.');
  const seen = new Set<string>();
  const charges = plan.orders.map(row => {
    if (!/^\d+$/.test(row.storeId) || typeof row.store !== 'string' || !Number.isSafeInteger(row.amountCents) || row.amountCents <= 0 ||
        typeof row.reported !== 'string' || typeof row.description !== 'string') throw new Error('Invalid order in review plan.');
    const url = new URL(row.href);
    if (url.origin !== 'https://www.doordash.com' || !/^\/merchant\/financials\/transactions\/[^/]+\/?$/.test(url.pathname) ||
        url.searchParams.get('store_id') !== row.storeId) throw new Error('Invalid order link in review plan.');
    const id = `${row.storeId}:${url.pathname.replace(/\/$/, '')}`;
    if (seen.has(id)) throw new Error('Duplicate order in review plan.');
    seen.add(id);
    return {
      id, platform: 'doordash' as const, storeName: row.store,
      orderId: url.pathname.split('/').filter(Boolean).slice(-1)[0] || id,
      orderDate: '', chargeDate: '', issueType: 'other' as const,
      itemsAffected: [], amount: row.amountCents / 100, customerId: '',
      customerNote: row.reported, hasPhotoEvidence: false, disputeDeadline: '',
    };
  });
  return { plan, charges };
}
