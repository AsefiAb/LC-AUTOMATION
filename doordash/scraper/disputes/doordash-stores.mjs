// All DoorDash stores under the merchant login, with their Financials →
// Transactions page. Scanning visits every selected store and verifies every
// results page; finding one order never ends the store or region scan.

export const DOORDASH_STORES = [
  { region: "CO", label: "CO-1", storeId: "1150071" },
  { region: "CO", label: "CO-2", storeId: "1150117" },
  { region: "CO", label: "CO-3", storeId: "1150076" },
  { region: "CO", label: "CO-4", storeId: "1148889" },
  { region: "CO", label: "CO-5", storeId: "1149986" },
  { region: "CO", label: "CO-6", storeId: "1149984" },
  { region: "CO", label: "CO-7", storeId: "1149050" },
  { region: "AZ", label: "AZ-1", storeId: "24667666" },
  { region: "AZ", label: "AZ-2", storeId: "24608604" },
  { region: "AZ", label: "AZ-3", storeId: "24608596" },
  { region: "AZ", label: "AZ-4", storeId: "24608662" },
  { region: "AZ", label: "AZ-5", storeId: "24608608" },
  { region: "AZ", label: "AZ-6", storeId: "24647724" },
  { region: "AZ", label: "AZ-7", storeId: "24608642" },
  { region: "TX", label: "TX-1", storeId: "34054619" },
  { region: "TX", label: "TX-2", storeId: "34054605" },
  { region: "TX", label: "TX-3", storeId: "34054615" },
  { region: "TX", label: "TX-4", storeId: "34054604" },
];

export function financialsUrl(storeId) {
  return `https://www.doordash.com/merchant/financials/transactions?store_id=${storeId}`;
}
