import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import type { BomDraft, ValidationIssue } from "@c2b/ts-types";
import type { ApprovalResult, VersionSummary } from "../lib/api";
import { G } from "./glossary";

/** What each blocking check means and what to do about it, in plain words. */
const ISSUE_HELP: Record<string, string> = {
  unresolved_question: "Answer this question in the panel on the left.",
  open_suggestion: "Accept or reject this suggestion in the panel on the left.",
  invalid_quantity: "Set a quantity above zero in the table.",
  unknown_uom: "Use a unit of measure the library knows (for example EA, M, KG).",
  circular_dependency: "An assembly contains itself. Check the structure of the CAD model.",
  duplicate_part_number_conflict:
    "The same part number is used for two different parts. Change one of the part numbers in the table.",
  unclassified_part: "Pick a class for this line so the BoM can describe and number it.",
};

export function ApproveDialog({
  open,
  onOpenChange,
  draft,
  versions,
  reopened,
  busy,
  onApprove,
  onGoTo,
  onOpenExports,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  draft: BomDraft;
  versions: VersionSummary[];
  /** True when the job was approved before and has changed since. */
  reopened: boolean;
  busy: boolean;
  onApprove: () => Promise<ApprovalResult | null>;
  onGoTo: (issue: ValidationIssue) => void;
  onOpenExports: () => void;
}) {
  const [result, setResult] = useState<ApprovalResult | null>(null);
  const blocking = draft.validation?.blocking_issues ?? [];
  const warnings = draft.validation?.warnings ?? [];
  const latest = versions.at(-1);

  async function approve() {
    const r = await onApprove();
    if (r) setResult(r);
  }

  function close(next: boolean) {
    if (!next) setResult(null);
    onOpenChange(next);
  }

  return (
    <Dialog.Root open={open} onOpenChange={close}>
      <Dialog.Portal>
        <Dialog.Overlay className="rv-overlay" />
        <Dialog.Content className="rv-dialog" aria-describedby="rv-approve-desc">
          <Dialog.Title>
            {result ? "BoM approved" : blocking.length > 0 ? "Approval is blocked" : "Approve this BoM"}
          </Dialog.Title>

          {result ? (
            <div className="stack" id="rv-approve-desc">
              <p>
                <G term="version">{`Version ${result.version.toString()}`}</G> is frozen.
                {result.unchanged ? " Nothing had changed, so no new version was made." : ""} You can download it in any
                format now.
              </p>
              <div className="actions">
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={() => {
                    close(false);
                    onOpenExports();
                  }}
                >
                  Download exports
                </button>
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => {
                    close(false);
                  }}
                >
                  Close
                </button>
              </div>
            </div>
          ) : (
            <div className="stack" id="rv-approve-desc">
              {blocking.length > 0 ? (
                <>
                  <p>
                    {blocking.length.toString()} thing{blocking.length === 1 ? " needs" : "s need"} your attention before this
                    BoM can be approved. Approval protects you from releasing a list with open questions.
                  </p>
                  <ul className="rv-issues">
                    {blocking.map((i, n) => (
                      <li key={`${i.code}-${i.line_id ?? ""}-${n.toString()}`}>
                        <div>
                          <strong>{i.message}</strong>
                          <div className="small muted">
                            {ISSUE_HELP[i.code] ?? "Resolve this item, then try again."}
                            {i.rule_id ? <span className="rv-mono"> {i.rule_id}</span> : null}
                          </div>
                        </div>
                        <button
                          type="button"
                          className="btn btn-secondary"
                          onClick={() => {
                            close(false);
                            onGoTo(i);
                          }}
                        >
                          Go there
                        </button>
                      </li>
                    ))}
                  </ul>
                </>
              ) : (
                <p>
                  Nothing blocks approval. Approving freezes <G term="version">a version</G> that never changes. If you
                  edit afterwards, a new draft starts and you can approve again.
                  {reopened && latest
                    ? ` Version ${latest.version.toString()} stays as it is.`
                    : ""}
                </p>
              )}

              {warnings.length > 0 ? (
                <details className="rv-warnings">
                  <summary>
                    {warnings.length.toString()} warning{warnings.length === 1 ? "" : "s"} (they do not block approval)
                  </summary>
                  <ul>
                    {warnings.map((w, n) => (
                      <li key={`${w.code}-${w.line_id ?? ""}-${n.toString()}`}>{w.message}</li>
                    ))}
                  </ul>
                </details>
              ) : null}

              <div className="actions">
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={blocking.length > 0 || busy}
                  onClick={() => {
                    void approve();
                  }}
                >
                  {busy ? "Approving…" : "Approve"}
                </button>
                <Dialog.Close className="btn btn-secondary">Cancel</Dialog.Close>
              </div>
            </div>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
