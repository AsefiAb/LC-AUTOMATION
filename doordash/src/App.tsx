import { useEffect, useMemo, useRef, useState } from "react";
import type { Decision, DisputeItem, ErrorCharge, Platform, Priority } from "./types";
import { sampleCharges } from "./sampleData";
import { assess } from "./lib/rules";
import { draftDispute } from "./lib/draft";
import { parseCsv } from "./lib/parse";
import { parsePlan, type ReviewPlan } from "./lib/plan";

const PLATFORM_LABEL: Record<Platform, string> = {
  doordash: "DoorDash",
  ubereats: "Uber Eats",
  grubhub: "Grubhub",
};

const PRIORITY_LABEL: Record<Priority, string> = {
  high: "High priority",
  medium: "Review charge",
  low: "Lower priority",
  none: "Not disputable",
};

function build(charges: ErrorCharge[]): DisputeItem[] {
  return charges
    .map((charge) => ({
      charge,
      assessment: assess(charge, charges),
      draft: draftDispute(charge),
      decision: "pending" as Decision,
    }))
    // Highest-value disputes first.
    .sort((a, b) => b.assessment.recoverable - a.assessment.recoverable);
}

type Tab = "review" | "approved" | "dismissed";

export function App() {
  const [items, setItems] = useState<DisputeItem[]>([]);
  const [reviewPlan, setReviewPlan] = useState<ReviewPlan | null>(null);
  const [uploadError, setUploadError] = useState("");
  const [sourceLabel, setSourceLabel] = useState("Upload a review plan or payout CSV to begin.");
  const [batchVersion, setBatchVersion] = useState(0);
  const [tab, setTab] = useState<Tab>("review");
  const [submittedFlash, setSubmittedFlash] = useState(0);
  const [settingsOpen, setSettingsOpen] = useState(false);
  // Optional automatic draft approval still requires a separate export and runner command.
  const [autoApprove, setAutoApprove] = useState(
    () => localStorage.getItem("dd_autoApprove") === "1",
  );
  const [termsAccepted, setTermsAccepted] = useState(
    () => localStorage.getItem("dd_termsAccepted") === "1",
  );
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    localStorage.setItem("dd_autoApprove", autoApprove ? "1" : "0");
    localStorage.setItem("dd_termsAccepted", termsAccepted ? "1" : "0");
  }, [autoApprove, termsAccepted]);

  // When auto-approve is active (and terms accepted), sweep any pending
  // reviewable charge to "approved" on a new import or when the option is enabled.
  // Manual decisions remain editable until the next import.
  const autoActive = autoApprove && termsAccepted;
  useEffect(() => {
    if (!autoActive) return;
    setItems((prev) => {
      let changed = false;
      const next = prev.map((it) => {
        if (it.decision === "pending" && it.assessment.disputable) {
          changed = true;
          return { ...it, decision: "approved" as Decision };
        }
        return it;
      });
      return changed ? next : prev;
    });
  }, [autoActive, batchVersion]);

  const setDecision = (id: string, decision: Decision) =>
    setItems((prev) =>
      prev.map((it) => (it.charge.id === id ? { ...it, decision } : it)),
    );

  const setDraft = (id: string, draft: string) =>
    setItems((prev) =>
      prev.map((it) => (it.charge.id === id ? { ...it, draft } : it)),
    );

  const submitApproved = () => {
    const approved = items.filter(it => it.decision === "approved");
    if (!approved.length) return;
    const payload = reviewPlan ? {
      ...reviewPlan,
      orders: reviewPlan.orders.map(row => {
        const key = `${row.storeId}:${new URL(row.href).pathname.replace(/\/$/, "")}`;
        const item = items.find(it => it.charge.id === key);
        return { ...row, approved: item?.decision === "approved", description: item?.draft || row.description };
      }),
    } : { kind: "drafts-for-manual-review", orders: approved.map(it => ({ ...it.charge, description: it.draft })) };
    const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = reviewPlan ? "approved-plan.json" : "dispute-drafts.json";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    setItems(prev => prev.map(it => it.decision === "approved" ? { ...it, decision: "exported" } : it));
    setSubmittedFlash(approved.length);
    setTab("approved");
  };

  const onUpload = async (file: File) => {
    setUploadError("");
    try {
      const text = await file.text();
      if (/\.json$/i.test(file.name)) {
        const { plan, charges } = parsePlan(text);
        const drafts = new Map(charges.map((charge, i) => [charge.id, plan.orders[i].description]));
        setItems(build(charges).map(it => ({ ...it, draft: drafts.get(it.charge.id) || it.draft })));
        setReviewPlan(plan);
        setSourceLabel(`${file.name} — ${plan.scope}`);
      } else {
        const hint: Platform = /uber/i.test(file.name) ? "ubereats" : /grub/i.test(file.name) ? "grubhub" : "doordash";
        const charges = parseCsv(text, hint);
        if (!charges.length) throw new Error("No valid charge rows found.");
        setItems(build(charges));
        setReviewPlan(null);
        setSourceLabel(`${file.name} — CSV drafts require manual portal entry.`);
      }
      setSubmittedFlash(0);
      setBatchVersion(value => value + 1);
      setTab("review");
    } catch (error) { setUploadError(error instanceof Error ? error.message : "Could not read file."); }
    finally { if (fileRef.current) fileRef.current.value = ""; }
  };

  const stats = useMemo(() => {
    const clawedBack = items.reduce((s, it) => s + it.charge.amount, 0);
    const disputable = items.filter((it) => it.assessment.disputable);
    const recoverable = disputable.reduce((s, it) => s + it.assessment.recoverable, 0);
    const approved = items
      .filter((it) => it.decision === "approved" || it.decision === "exported")
      .reduce((s, it) => s + it.assessment.recoverable, 0);
    return {
      clawedBack,
      recoverable,
      approved,
      count: items.length,
      disputableCount: disputable.length,
    };
  }, [items]);

  const visible = items.filter((it) => {
    if (tab === "review") return it.decision === "pending";
    if (tab === "approved")
      return it.decision === "approved" || it.decision === "exported";
    return it.decision === "skipped" || !it.assessment.disputable;
  });

  const approvedCount = items.filter((it) => it.decision === "approved").length;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo">◈</span>
          <div>
            <h1>DisputeDash</h1>
            <p>Recover the chargebacks the delivery apps take from your kitchen.</p>
          </div>
        </div>
        <div className="actions">
          <input
            ref={fileRef}
            type="file"
            accept=".csv,.json"
            hidden
            onChange={(e) => e.target.files?.[0] && onUpload(e.target.files[0])}
          />
          <button className="ghost" onClick={() => fileRef.current?.click()}>
            Upload review plan / CSV
          </button>
          <button className="ghost" onClick={() => { setItems(build(sampleCharges)); setReviewPlan(null); setSourceLabel("SAMPLE DATA — demonstration only"); setSubmittedFlash(0); setBatchVersion(value => value + 1); }}>
            Load sample data
          </button>
          <button className="ghost" onClick={() => setSettingsOpen(true)}>
            ⚙ Settings
          </button>
        </div>
      </header>

      <p className="auto-banner">{sourceLabel}</p>
      {uploadError && <p role="alert" className="flash">{uploadError}</p>}
      {autoActive && (
        <div className="auto-banner">
          ⚡ Auto-approve is <strong>on</strong> — every disputable charge is approved
          automatically. You still review the queue and export the drafts.
          <button onClick={() => setSettingsOpen(true)}>manage</button>
        </div>
      )}

      <section className="stats">
        <StatCard label="Clawed back this period" value={money(stats.clawedBack)} tone="bad" />
        <StatCard
          label="Amount to review"
          value={money(stats.recoverable)}
          sub={`${stats.disputableCount} of ${stats.count} charges ready for review`}
          tone="good"
        />
        <StatCard label="Approved for review" value={money(stats.approved)} tone="accent" />
      </section>

      {submittedFlash > 0 && (
        <div className="flash">
          ✓ {submittedFlash} draft{submittedFlash > 1 ? "s" : ""} exported. Nothing has been filed with a delivery platform.
          <button onClick={() => setSubmittedFlash(0)}>dismiss</button>
        </div>
      )}

      <nav className="tabs">
        <TabBtn active={tab === "review"} onClick={() => setTab("review")}>
          To review ({items.filter((i) => i.decision === "pending").length})
        </TabBtn>
        <TabBtn active={tab === "approved"} onClick={() => setTab("approved")}>
          Approved ({items.filter((i) => i.decision === "approved" || i.decision === "exported").length})
        </TabBtn>
        <TabBtn active={tab === "dismissed"} onClick={() => setTab("dismissed")}>
          Skipped / not disputable
        </TabBtn>
        <div className="spacer" />
        {approvedCount > 0 && (
          <button className="primary" onClick={submitApproved}>
            Export {approvedCount} approved →
          </button>
        )}
      </nav>

      <main className="list">
        {visible.length === 0 && <p className="empty">Nothing here yet.</p>}
        {visible.map((it) => (
          <DisputeCard
            key={it.charge.id}
            item={it}
            onApprove={() => setDecision(it.charge.id, "approved")}
            onSkip={() => setDecision(it.charge.id, "skipped")}
            onReset={() => setDecision(it.charge.id, "pending")}
            onDraftChange={(v) => setDraft(it.charge.id, v)}
          />
        ))}
      </main>

      {settingsOpen && (
        <SettingsModal
          autoApprove={autoApprove}
          termsAccepted={termsAccepted}
          onToggleAuto={setAutoApprove}
          onAcceptTerms={setTermsAccepted}
          onClose={() => setSettingsOpen(false)}
        />
      )}
    </div>
  );
}

function SettingsModal({
  autoApprove,
  termsAccepted,
  onToggleAuto,
  onAcceptTerms,
  onClose,
}: {
  autoApprove: boolean;
  termsAccepted: boolean;
  onToggleAuto: (v: boolean) => void;
  onAcceptTerms: (v: boolean) => void;
  onClose: () => void;
}) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>Settings</h2>
          <button className="x" onClick={onClose}>
            ×
          </button>
        </div>

        <div className="setting-row">
          <div>
            <div className="setting-title">Auto-approve disputable charges</div>
            <div className="setting-desc">
              Automatically approve every charge the engine flags as disputable, so you
              only have to export the drafts. Requires accepting the terms below.
            </div>
          </div>
          <label className={`switch ${!termsAccepted ? "disabled" : ""}`}>
            <input
              type="checkbox"
              checked={autoApprove}
              disabled={!termsAccepted}
              onChange={(e) => onToggleAuto(e.target.checked)}
            />
            <span className="slider" />
          </label>
        </div>

        <div className="terms-box">
          <div className="terms-title">Draft review</div>
          <p>
            By enabling auto-approve you authorize DisputeDash to prepare and queue
            disputes on your behalf for charges our system flags. You remain responsible
            for the accuracy of the underlying claims, retain final control via the export
            step, and confirm you are authorized to contest charges on this restaurant's
            merchant accounts. Disputes are prepared from your data and platform records;
            outcomes are determined solely by DoorDash, Uber Eats, and Grubhub.
          </p>
          <label className="terms-accept">
            <input
              type="checkbox"
              checked={termsAccepted}
              onChange={(e) => {
                onAcceptTerms(e.target.checked);
                if (!e.target.checked) onToggleAuto(false);
              }}
            />
            <span>I have read and accept these terms.</span>
          </label>
        </div>

        <button className="primary modal-done" onClick={onClose}>
          Done
        </button>
      </div>
    </div>
  );
}

function StatCard({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub?: string;
  tone: "good" | "bad" | "accent";
}) {
  return (
    <div className={`stat ${tone}`}>
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

function TabBtn({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button className={`tab ${active ? "active" : ""}`} onClick={onClick}>
      {children}
    </button>
  );
}

function DisputeCard({
  item,
  onApprove,
  onSkip,
  onReset,
  onDraftChange,
}: {
  item: DisputeItem;
  onApprove: () => void;
  onSkip: () => void;
  onReset: () => void;
  onDraftChange: (v: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const { charge, assessment, decision } = item;

  return (
    <article className={`card pri-${assessment.priority}`}>
      <div className="card-head">
        <div className="badges">
          <span className={`badge plat-${charge.platform}`}>
            {PLATFORM_LABEL[charge.platform]}
          </span>
          <span className={`badge pri pri-${assessment.priority}`}>
            {PRIORITY_LABEL[assessment.priority]}
          </span>
          {decision === "exported" && <span className="badge sent">Exported — not filed</span>}
          {decision === "approved" && <span className="badge appr">Approved</span>}
        </div>
        <div className="amount">
          <span className="amount-charged">{money(charge.amount)}</span>
          {assessment.disputable && (
            <span className="amount-rec">{money(assessment.recoverable)} requested</span>
          )}
        </div>
      </div>

      <div className="card-order">
        <strong>{charge.storeName} · {charge.orderId}</strong> · {charge.itemsAffected.join(", ") || "—"} · ordered{" "}
        {charge.orderDate}
        {assessment.disputable && assessment.daysLeft >= 0 && (
          <span className={`window ${assessment.daysLeft <= 3 ? "urgent" : ""}`}>
            {assessment.daysLeft} day{assessment.daysLeft === 1 ? "" : "s"} left to file
          </span>
        )}
      </div>

      <ul className="reasons">
        {assessment.reasons.map((r, i) => (
          <li key={i}>{r}</li>
        ))}
      </ul>

      {assessment.disputable && (
        <>
          <button className="link" onClick={() => setOpen((o) => !o)}>
            {open ? "▾ Hide" : "▸ Review"} the drafted dispute
          </button>
          {open && (
            <textarea
              className="draft"
              value={item.draft}
              onChange={(e) => onDraftChange(e.target.value)}
              rows={9}
            />
          )}
        </>
      )}

      <div className="card-actions">
        {decision === "pending" && assessment.disputable && (
          <>
            <button className="approve" onClick={onApprove}>
              Approve dispute
            </button>
            <button className="skip" onClick={onSkip}>
              Skip
            </button>
          </>
        )}
        {decision === "approved" && (
          <button className="undo" onClick={onReset}>
            Undo approval
          </button>
        )}
        {decision === "skipped" && (
          <button className="undo" onClick={onReset}>
            Move back to review
          </button>
        )}
        {!assessment.disputable && <span className="muted">No action needed.</span>}
      </div>
    </article>
  );
}

function money(n: number): string {
  return n.toLocaleString("en-US", { style: "currency", currency: "USD" });
}
