import Papa from "papaparse";
import type { ErrorCharge, IssueType, Platform } from "../types";

// Each platform's export uses different column names. This maps the common
// header variants onto our normalized ErrorCharge shape. Extend as we see real
// exports from each portal.
const FIELD_ALIASES: Record<keyof ErrorCharge | "amountRaw", string[]> = {
  id: ["id", "error id", "charge id", "adjustment id"],
  platform: ["platform", "source"],
  storeName: ["store", "store name", "restaurant", "location"],
  orderId: ["order id", "order #", "order number", "transaction id"],
  orderDate: ["order date", "date", "order placed"],
  chargeDate: ["charge date", "adjustment date", "payout date", "posted date"],
  issueType: ["issue", "issue type", "reason", "error category", "category"],
  itemsAffected: ["items", "item", "items affected", "product"],
  amount: [],
  amountRaw: ["amount", "error charge", "adjustment amount", "deduction", "charged amount"],
  customerId: ["customer", "customer id", "consumer id"],
  customerNote: ["customer note", "note", "reason detail", "comment"],
  hasPhotoEvidence: ["photo", "has photo", "evidence", "photo evidence"],
  disputeDeadline: ["dispute deadline", "deadline", "dispute by", "expires"],
};

function pick(row: Record<string, string>, aliases: string[]): string {
  for (const alias of aliases) {
    if (row[alias] != null && row[alias] !== "") return row[alias];
  }
  return "";
}

function normalizeIssue(raw: string): IssueType {
  const s = raw.toLowerCase();
  if (/never|not deliver|no.show|undelivered/.test(s)) return "never_delivered";
  if (/whole|full order|entire|total refund/.test(s)) return "whole_order_refund";
  if (/missing|not (included|received)|didn.t get/.test(s)) return "missing_item";
  if (/wrong|incorrect|different/.test(s)) return "wrong_item";
  if (/quality|cold|soggy|stale|undercook|overcook/.test(s)) return "quality";
  return "other";
}

function guessPlatform(raw: string, fallback: Platform): Platform {
  const s = raw.toLowerCase();
  if (s.includes("door")) return "doordash";
  if (s.includes("uber")) return "ubereats";
  if (s.includes("grub")) return "grubhub";
  return fallback;
}

function parseAmount(raw: string): number {
  const cleaned = raw.trim().replace(/[$,]/g, "");
  if (!/^-?\d+(\.\d{1,2})?$/.test(cleaned)) throw new Error("Invalid charge amount.");
  const value = Math.abs(Number(cleaned));
  if (!Number.isFinite(value) || value <= 0) throw new Error("Charge amounts must be positive.");
  return value;
}

function dateOnly(raw: string): string {
  if (!raw.trim()) return "";
  const s = raw.trim();
  const us = s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
  const iso = us ? `${us[3]}-${us[1].padStart(2, '0')}-${us[2].padStart(2, '0')}` : s;
  const parsed = new Date(iso + 'T00:00:00Z');
  if (!/^\d{4}-\d{2}-\d{2}$/.test(iso) || !Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== iso) {
    throw new Error("Dates must be valid YYYY-MM-DD or M/D/YYYY values.");
  }
  return iso;
}

/**
 * Parse an uploaded merchant CSV export into normalized error charges.
 * `platformHint` is used when the file doesn't name the platform itself.
 */
export function parseCsv(text: string, platformHint: Platform): ErrorCharge[] {
  const { data, errors } = Papa.parse<Record<string, string>>(text, {
    header: true,
    skipEmptyLines: true,
  });

  if (errors.length) throw new Error(`CSV could not be read: ${errors[0].message}`);
  const seen = new Set<string>();
  return data.map((source, i) => {
    const row = Object.fromEntries(Object.entries(source).map(([key, value]) => [key.trim().toLowerCase(), value]));
    const orderId = pick(row, FIELD_ALIASES.orderId);
    if (!orderId) throw new Error(`Row ${i + 2}: missing order ID.`);
    const id = [guessPlatform(pick(row, FIELD_ALIASES.platform), platformHint), pick(row, FIELD_ALIASES.storeName), pick(row, FIELD_ALIASES.id) || orderId].join(":");
    if (seen.has(id)) throw new Error(`Row ${i + 2}: duplicate charge ID for this store.`);
    seen.add(id);
    const chargeDate = pick(row, FIELD_ALIASES.chargeDate) || pick(row, FIELD_ALIASES.orderDate);
    const orderDate = pick(row, FIELD_ALIASES.orderDate) || chargeDate;
    const rawDeadline = pick(row, FIELD_ALIASES.disputeDeadline);
    const items = pick(row, FIELD_ALIASES.itemsAffected);
    const photo = pick(row, FIELD_ALIASES.hasPhotoEvidence).toLowerCase();

    return {
      id,
      platform: guessPlatform(pick(row, FIELD_ALIASES.platform), platformHint),
      storeName: pick(row, FIELD_ALIASES.storeName) || "My Store",
      orderId,
      orderDate: dateOnly(orderDate),
      chargeDate: dateOnly(chargeDate),
      issueType: normalizeIssue(pick(row, FIELD_ALIASES.issueType)),
      itemsAffected: items ? items.split(/[;|]/).map((s) => s.trim()) : [],
      amount: parseAmount(pick(row, FIELD_ALIASES.amountRaw)),
      customerId: pick(row, FIELD_ALIASES.customerId),
      customerNote: pick(row, FIELD_ALIASES.customerNote),
      hasPhotoEvidence: /^(yes|y|true|1)$/.test(photo),
      // A missing deadline stays unknown; the portal determines eligibility.
      disputeDeadline: dateOnly(rawDeadline),
    };
  });
}
