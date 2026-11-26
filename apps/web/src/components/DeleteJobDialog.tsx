import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { ApiError, deleteJob } from "../lib/api";

/**
 * "Delete this job now": removes the job, its files and every version, immediately (ADR-021).
 * Cancel has the initial focus; the destructive button says what it does.
 */
export function DeleteJobDialog({
  jobId,
  token,
  disabled,
  onDeleted,
  triggerClassName = "btn btn-quiet",
}: {
  jobId: string;
  token: string;
  /** True while the job is still being built: deleting then is refused by the server. */
  disabled?: boolean;
  onDeleted: () => void;
  triggerClassName?: string;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      await deleteJob(jobId, token);
      setOpen(false);
      onDeleted();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not delete the job. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) setError(null);
      }}
    >
      <Dialog.Trigger className={triggerClassName} disabled={disabled}>
        Delete this job now
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="rv-overlay" />
        <Dialog.Content
          className="rv-dialog"
          aria-describedby="delete-job-desc"
          onOpenAutoFocus={(e) => {
            e.preventDefault();
            document.getElementById("delete-job-cancel")?.focus();
          }}
        >
          <Dialog.Title>Delete this job?</Dialog.Title>
          <div className="stack" id="delete-job-desc">
            <p>
              This removes your uploaded files, the draft, every approved version and the 3D
              model from the server, right now. It cannot be undone. Download your exports first
              if you still need them.
            </p>
            {error ? (
              <p role="alert" className="alert alert-danger">
                {error}
              </p>
            ) : null}
            <div className="actions">
              <button
                type="button"
                className="btn btn-danger"
                disabled={busy}
                onClick={() => {
                  void confirm();
                }}
              >
                {busy ? "Deleting…" : "Delete everything"}
              </button>
              <Dialog.Close id="delete-job-cancel" className="btn btn-secondary">
                Cancel
              </Dialog.Close>
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
